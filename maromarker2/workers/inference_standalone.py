"""Standalone inference for U-YOLO models exported to ONNX.

Self-contained: copy this file next to the exported `<model>.onnx` and
`inference_args.yaml` (both written by `export_model.py`) into any project.

Requirements: numpy, opencv-python, onnxruntime (or onnxruntime-gpu), pyyaml.

    from inference_standalone import OnnxPoseDetector, draw_detections

    detector = OnnxPoseDetector("path/to/onnx_export")
    detections = detector.predict("image.jpg")
    for d in detections:
        print(d.label, d.path, d.conf, d.box, d.keypoints)

The model runs on the full image at its native resolution (dynamic input axes, zero
padded to a multiple of the network stride), as in the evaluation of the training
code. Everything the raw network output needs afterwards - hierarchy descent, NMS,
suppression of nested boxes - is implemented here and parametrised by
`inference_args.yaml`.

Command line: python inference_standalone.py MODEL_DIR IMAGE [--output OUT.jpg]
"""
import argparse

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import yaml

STRIDE = 32  # largest stride of the YOLOv8 backbone; input sides must be multiples of it
INFERENCE_ARGS_FILENAME = "inference_args.yaml"


@dataclass
class Detection:
    """One detected animal.

    Attributes:
        box: (x1, y1, x2, y2) in original image pixels.
        conf: Score of the predicted (finest confident) class.
        class_id: Index of the predicted class in `names`.
        label: Name of the predicted class.
        path: Taxonomy path from the top-level group down to `label`
            (just `[label]` for non-hierarchical models).
        keypoints: (n_kpt, 3) array of (x, y, visibility) in original image pixels.
            For two keypoints these are head and tail.
        symmetric: True if the class has symmetric keypoints, i.e. the order of
            the two keypoints (head/tail) carries no meaning.
        scores: Per-class scores of the network for this detection, shape (nc,).
    """
    box: np.ndarray
    conf: float
    class_id: int
    label: str
    path: list[str]
    keypoints: np.ndarray
    symmetric: bool
    scores: np.ndarray = field(repr=False)


class _Taxonomy:
    """Descent tables built from the `hierarchy_training` entry of inference_args.yaml."""

    def __init__(self, hierarchy_training: dict, class_map: dict[str, int]):
        parents: dict[str, str | None] = {}
        children: dict[str, list[str]] = {}

        def add(name, value, parent):
            parents[name] = parent
            children.setdefault(name, [])
            if parent is not None:
                children[parent].append(name)
            kids = value.get("children") if isinstance(value, dict) else None
            if isinstance(kids, list):
                for kid in kids:
                    add(kid, None, name)
            elif isinstance(kids, dict):
                for kid, kid_value in kids.items():
                    add(kid, kid_value, name)

        for name, value in hierarchy_training["hierarchy"].items():
            add(name, value, None)

        self.parents = parents
        nc = len(class_map)
        child_lists = [[] for _ in range(nc)]
        top_level = []
        for name, idx in class_map.items():
            child_lists[idx] = [class_map[c] for c in children.get(name, []) if c in class_map]
            if parents.get(name) is None:
                top_level.append(idx)

        width = max((len(c) for c in child_lists), default=0) or 1
        self.children = np.zeros((nc, width), dtype=np.int64)
        self.child_mask = np.zeros((nc, width), dtype=bool)
        for i, ch in enumerate(child_lists):
            self.children[i, : len(ch)] = ch
            self.child_mask[i, : len(ch)] = True
        self.top_level = np.array(sorted(top_level), dtype=np.int64)

        def depth(name):
            d = 1
            while parents.get(name) is not None:
                name, d = parents[name], d + 1
            return d

        self.depth = max(depth(n) for n in class_map)

    def path(self, name: str) -> list[str]:
        chain = [name]
        while self.parents.get(chain[-1]) is not None:
            chain.append(self.parents[chain[-1]])
        return chain[::-1]


class OnnxPoseDetector:
    """ONNX pose detector with the postprocessing of the training code.

    Args:
        model_dir: Directory holding `inference_args.yaml` and the ONNX file it names.
        conf: Confidence threshold; defaults to `thresholds.conf` of the args file.
        descent_conf: Score a child must reach for the hierarchy descent to continue
            into it; defaults to `thresholds.hierarchy_descent_conf` of the args file.
        providers: onnxruntime execution providers; defaults to CUDA if available, else CPU.
    """

    def __init__(self, model_dir, conf: float | None = None, descent_conf: float | None = None, providers=None):
        model_dir = Path(model_dir)
        with open(model_dir / INFERENCE_ARGS_FILENAME) as f:
            self.args = yaml.safe_load(f)
        args = self.args

        if args["channels"] != 3:
            raise NotImplementedError("Only 3-channel (single RGB image) models are supported")

        self.names = {int(i): n for i, n in args["names"].items()}
        self.nc = len(self.names)
        self.kpt_shape = tuple(args["kpt_shape"])
        self.n_kpt_values = self.kpt_shape[0] * self.kpt_shape[1]
        self.use_softmax = bool(args["use_softmax"])
        self.hierarchical = bool(args["hierarchical_labels"])
        self.symmetric_ids = set(args["symmetric_class_ids"])

        post = args["postprocessing"]
        self.nms_iou = float(post["nms_iou"])
        self.max_det = int(post["max_det"])
        self.contain_thresh = float(post["hierarchy_contain_thresh"])

        thr = args["thresholds"]
        self.conf = float(conf if conf is not None else thr["conf"] if thr["conf"] is not None else "nan")
        self.descent_conf = float(
            descent_conf if descent_conf is not None
            else thr["hierarchy_descent_conf"] if thr["hierarchy_descent_conf"] is not None else "nan"
        )
        if np.isnan(self.conf):
            raise ValueError("No confidence threshold in inference_args.yaml; pass `conf` explicitly")
        if self.hierarchical and np.isnan(self.descent_conf):
            raise ValueError("No hierarchy descent threshold in inference_args.yaml; pass `descent_conf` explicitly")

        self.taxonomy = None
        if self.hierarchical:
            class_map = {name: idx for idx, name in self.names.items()}
            self.taxonomy = _Taxonomy(args["hierarchy_training"], class_map)

        prep = args["preprocessing"]
        self.apply_white_balance = bool(prep["apply_white_balance"])
        self.clahe = None
        if prep["apply_clahe"]:
            grid = int(prep["clahe_grid_size"])
            self.clahe = cv2.createCLAHE(clipLimit=float(prep["clahe_clip_limit"]), tileGridSize=(grid, grid))

        if providers is None:
            available = ort.get_available_providers()
            providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in available]
        self.session = ort.InferenceSession(str(model_dir / args["model"]["onnx_file"]), providers=providers)
        self.input_name = self.session.get_inputs()[0].name

    # ── preprocessing ─────────────────────────────────────────────────────────

    def preprocess(self, img_rgb: np.ndarray) -> np.ndarray:
        """Apply the white balance / CLAHE configured at training time (RGB uint8 in and out)."""
        if not (self.apply_white_balance or self.clahe is not None):
            return img_rgb
        l, a, b = cv2.split(cv2.cvtColor(img_rgb, cv2.COLOR_RGB2LAB))
        if self.apply_white_balance:  # shift the means of the a and b channels to 128
            a = np.clip(a.astype(np.float32) - (a.mean() - 128), 0, 255).astype(np.uint8)
            b = np.clip(b.astype(np.float32) - (b.mean() - 128), 0, 255).astype(np.uint8)
        if self.clahe is not None:
            l = self.clahe.apply(l)
        return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB)

    def _to_tensor(self, imgs_rgb: list[np.ndarray]) -> np.ndarray:
        """Stack RGB images into one (B, 3, H, W) float tensor, zero padded bottom/right to a common stride-multiple size."""
        h = max(img.shape[0] for img in imgs_rgb)
        w = max(img.shape[1] for img in imgs_rgb)
        h, w = h + -h % STRIDE, w + -w % STRIDE
        batch = np.zeros((len(imgs_rgb), 3, h, w), dtype=np.float32)
        for i, img in enumerate(imgs_rgb):
            batch[i, :, : img.shape[0], : img.shape[1]] = img.transpose(2, 0, 1)
        return batch / 255.0

    # ── inference ─────────────────────────────────────────────────────────────

    def predict(self, image, scale: float = 1.0) -> list[Detection]:
        """Detect animals in one image.

        Args:
            image: Path to an image file, or an RGB uint8 array of shape (H, W, 3).
            scale: Resize factor applied before the network (e.g. 0.5 for speed);
                outputs are mapped back to original image pixels. The model was trained
                at native resolution, so 1.0 is recommended.

        Returns:
            Detections sorted by descending confidence.
        """
        return self.predict_batch([image], scale=scale)[0]

    def predict_batch(self, images, scale: float = 1.0) -> list[list[Detection]]:
        """Detect animals in several images with a single network call.

        Images may differ in size; they are zero padded to the largest one, so batches of
        similarly sized images waste the least memory. Memory grows linearly with the batch
        size (a native-resolution image is ~150 MB as float32 input alone).

        Args:
            images: Sequence of image paths and/or RGB uint8 arrays of shape (H, W, 3).
            scale: See `predict`.

        Returns:
            One detection list per image, in input order.
        """
        if len(images) == 0:
            return []
        prepared, orig_sizes = [], []
        for image in images:
            if isinstance(image, (str, Path)):
                bgr = cv2.imread(str(image))
                if bgr is None:
                    raise FileNotFoundError(f"Could not read image: {image}")
                img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            else:
                img = np.asarray(image)
            orig_h, orig_w = img.shape[:2]
            orig_sizes.append((orig_w, orig_h))
            if scale != 1.0:
                img = cv2.resize(img, (round(orig_w * scale), round(orig_h * scale)), interpolation=cv2.INTER_AREA)
            prepared.append(self.preprocess(img))

        out = self.session.run(None, {self.input_name: self._to_tensor(prepared)})[0]  # (B, channels, anchors)
        results = []
        for raw, (orig_w, orig_h) in zip(out, orig_sizes):
            detections = self._postprocess(raw.T)
            for d in detections:
                d.box = np.clip(d.box / scale, 0, [orig_w, orig_h, orig_w, orig_h])
                d.keypoints[:, :2] /= scale
            results.append(detections)
        return results

    def _postprocess(self, raw: np.ndarray) -> list[Detection]:
        """Turn the raw (anchors, 4 + class channels + keypoint values) output into detections."""
        nc = self.nc
        scores = raw[:, 4 : 4 + nc]  # with softmax, the extra background channel after these is dropped
        kpts = raw[:, 4 + nc + (1 if self.use_softmax else 0) :]

        # the kept class' score never exceeds the best score of any class, so this prefilter is exact
        keep = scores.max(1) > self.conf
        boxes_xywh, scores, kpts = raw[keep, :4], scores[keep], kpts[keep]
        if len(scores) == 0:
            return []

        if self.hierarchical:
            cls = self._descend(scores)
        else:
            cls = scores.argmax(1)
        conf = scores[np.arange(len(scores)), cls]
        above = conf > self.conf
        boxes_xywh, scores, kpts, cls, conf = boxes_xywh[above], scores[above], kpts[above], cls[above], conf[above]
        if len(conf) == 0:
            return []

        boxes = np.concatenate([boxes_xywh[:, :2] - boxes_xywh[:, 2:] / 2, boxes_xywh[:, :2] + boxes_xywh[:, 2:] / 2], 1)

        order = conf.argsort()[::-1][:30000]  # same candidate cap as ultralytics' NMS
        boxes, scores, kpts, cls, conf = boxes[order], scores[order], kpts[order], cls[order], conf[order]
        keep = _nms(boxes, conf, self.nms_iou)[: self.max_det]  # class-agnostic
        boxes, scores, kpts, cls, conf = boxes[keep], scores[keep], kpts[keep], cls[keep], conf[keep]
        if self.hierarchical:
            keep = _suppress_contained(boxes, conf, self.contain_thresh)
            boxes, scores, kpts, cls, conf = boxes[keep], scores[keep], kpts[keep], cls[keep], conf[keep]

        detections = []
        for i in range(len(conf)):
            name = self.names[int(cls[i])]
            detections.append(Detection(
                box=boxes[i].copy(),
                conf=float(conf[i]),
                class_id=int(cls[i]),
                label=name,
                path=self.taxonomy.path(name) if self.taxonomy else [name],
                keypoints=kpts[i].reshape(*self.kpt_shape).copy(),
                symmetric=int(cls[i]) in self.symmetric_ids,
                scores=scores[i].copy(),
            ))
        return detections

    def _descend(self, scores: np.ndarray) -> np.ndarray:
        """Greedy root-to-leaf walk per anchor: start at the best top-level class and move to
        the best-scoring child while that child reaches `descent_conf`. Returns class indices."""
        tax = self.taxonomy
        rows = np.arange(len(scores))
        cur = tax.top_level[scores[:, tax.top_level].argmax(1)]
        for _ in range(tax.depth):
            ch = tax.children[cur]
            ch_scores = np.where(tax.child_mask[cur], np.take_along_axis(scores, ch, 1), -1.0)
            best = ch_scores.argmax(1)
            descend = ch_scores[rows, best] >= self.descent_conf
            cur = np.where(descend, ch[rows, best], cur)
        return cur


def _nms(boxes: np.ndarray, conf: np.ndarray, iou_thresh: float) -> np.ndarray:
    """Greedy IoU suppression on xyxy boxes already sorted by descending confidence."""
    areas = (boxes[:, 2] - boxes[:, 0]).clip(0) * (boxes[:, 3] - boxes[:, 1]).clip(0)
    order = np.arange(len(boxes))
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        rest = order[1:]
        if rest.size == 0:
            break
        wh = (np.minimum(boxes[i, 2:], boxes[rest, 2:]) - np.maximum(boxes[i, :2], boxes[rest, :2])).clip(0)
        inter = wh[:, 0] * wh[:, 1]
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thresh]
    return np.array(keep, dtype=np.int64)


def _suppress_contained(boxes: np.ndarray, conf: np.ndarray, thresh: float) -> np.ndarray:
    """Drop boxes nested inside a higher-confidence box (intersection over the smaller area).

    IoU-based NMS misses these because IoU stays low when a small box sits inside a big one.
    """
    n = len(boxes)
    if n <= 1:
        return np.arange(n)
    areas = (boxes[:, 2] - boxes[:, 0]).clip(0) * (boxes[:, 3] - boxes[:, 1]).clip(0)
    lt = np.maximum(boxes[:, None, :2], boxes[None, :, :2])
    rb = np.minimum(boxes[:, None, 2:], boxes[None, :, 2:])
    wh = (rb - lt).clip(0)
    containment = wh[..., 0] * wh[..., 1] / np.maximum(np.minimum(areas[:, None], areas[None, :]), 1e-9)

    keep = np.ones(n, dtype=bool)
    for i in conf.argsort()[::-1]:
        if not keep[i]:
            continue
        nested = (containment[i] >= thresh) & keep
        nested[i] = False
        keep &= ~nested
    return np.flatnonzero(keep)


def draw_detections(
    image_rgb: np.ndarray, detections: list[Detection], thickness: int | None = None, opacity: float = 0.75
) -> np.ndarray:
    """Return a copy of `image_rgb` with boxes, keypoints and "label conf" captions drawn.

    `opacity` (0-1) is the weight of the drawn annotations over the image.
    """
    base_img = np.ascontiguousarray(image_rgb)
    out = base_img.copy()
    thickness = thickness or max(2, round(max(out.shape[:2]) / 800))
    palette = [(230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48),
               (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212)]
    for d in detections:
        color = palette[sum(map(ord, d.path[0])) % len(palette)]
        x1, y1, x2, y2 = (int(round(v)) for v in d.box)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, thickness)
        pts = [(int(round(x)), int(round(y))) for x, y, _ in d.keypoints]
        for k, p in enumerate(pts):
            cv2.circle(out, p, thickness * 2, (255, 255, 255) if k == 0 else (0, 0, 0), -1)
        caption = f"{d.label} {d.conf:.2f}"
        scale = thickness / 3
        (tw, th), base = cv2.getTextSize(caption, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        cv2.rectangle(out, (x1, max(y1 - th - base, 0)), (x1 + tw, max(y1, th + base)), color, -1)
        cv2.putText(out, caption, (x1, max(y1 - base, th)), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness)
    return cv2.addWeighted(out, opacity, base_img, 1 - opacity, 0)


def main():
    parser = argparse.ArgumentParser(description="Run an exported U-YOLO ONNX model on one image")
    parser.add_argument("model_dir", help="Directory with the .onnx file and inference_args.yaml")
    parser.add_argument("image", help="Input image")
    parser.add_argument("--output", default=None, help="Optional path to save the annotated image")
    parser.add_argument("--conf", type=float, default=None, help="Override confidence threshold")
    parser.add_argument("--descent_conf", type=float, default=None, help="Override hierarchy descent threshold")
    parser.add_argument("--scale", type=float, default=1.0, help="Resize factor before inference")
    cli = parser.parse_args()

    detector = OnnxPoseDetector(cli.model_dir, conf=cli.conf, descent_conf=cli.descent_conf)
    detections = detector.predict(cli.image, scale=cli.scale)
    for d in detections:
        print(f"{'/'.join(d.path)}  conf={d.conf:.3f}  box={d.box.round(1).tolist()}")
    print(f"{len(detections)} detections")

    if cli.output:
        img = cv2.cvtColor(cv2.imread(cli.image), cv2.COLOR_BGR2RGB)
        cv2.imwrite(cli.output, cv2.cvtColor(draw_detections(img, detections), cv2.COLOR_RGB2BGR))


if __name__ == "__main__":
    main()
