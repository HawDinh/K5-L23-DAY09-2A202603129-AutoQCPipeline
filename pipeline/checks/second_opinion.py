"""CHECK 6 — Ý kiến thứ hai: so nhãn với một model LỚN HƠN (yolo26l).   ★★★ (khó nhất, đáng điểm nhất)

Test:  pytest tests/test_second_opinion.py -q

Máy kiểm quy tắc, người kiểm ý nghĩa (slide). Các lỗi như thiếu nhãn / gán nhầm lớp hợp lệ / box lỏng
KHÔNG bắt được bằng quy tắc cú pháp. Ta nhờ một model mạnh hơn "nhìn lại" và CHỈ CẢNH BÁO (model cũng có thể sai):
những ảnh bị cờ sẽ vào hàng đợi cho NGƯỜI duyệt.

`preds[stem]` = list `[id_lớp, conf, cx, cy, w, h]` của model (lấy qua `get_preds(ds, cfg, "checker")` nếu preds=None).
`L = ds.valid_boxes(stem)` = list `(id_lớp, cx, cy, w, h)` của nhãn (đã bỏ dòng hỏng).
Ngưỡng ở `cfg["second_opinion"]`: conf, min_area_px, fp_conf, iou_missing, iou_match, iou_loose.
"""
from ..common import WARN, Issue, iou_xywh, parse_yolo_line
from ..model_cache import get_preds


def run(ds, cfg, preds=None):
    if preds is None:
        preds = get_preds(ds, cfg, "checker")

    so_cfg = cfg.get("second_opinion", {})
    conf_thresh = so_cfg.get("conf", 0.25)
    min_area_px = so_cfg.get("min_area_px", 600)
    fp_conf = so_cfg.get("fp_conf", 0.1)
    iou_missing = so_cfg.get("iou_missing", 0.3)
    iou_match = so_cfg.get("iou_match", 0.5)
    iou_loose = so_cfg.get("iou_loose", 0.5)

    issues = []

    for img_p in ds.image_files():
        stem = img_p.stem
        if stem not in preds:
            continue
        if not ds.label_path(stem).exists():
            continue

        size = ds.image_size(stem)
        if size is None:
            continue
        img_w, img_h = size

        raw_label_lines = ds.read_label_lines(stem)
        if raw_label_lines is None:
            continue

        valid_labels = []
        for line_no, text in raw_label_lines:
            try:
                c, cx, cy, w, h = parse_yolo_line(text)
            except ValueError:
                continue
            if 0 <= c < len(ds.classes) and w > 0 and h > 0:
                valid_labels.append((line_no, c, (cx, cy, w, h)))

        raw_preds = preds.get(stem, [])

        # Lọc dự đoán cho MISSING / LOOSE_BOX / WRONG_CLASS
        P = []
        for pr in raw_preds:
            p_cls, p_conf, pcx, pcy, pw, ph = pr[0], pr[1], pr[2], pr[3], pr[4], pr[5]
            area_px = (pw * img_w) * (ph * img_h)
            if p_conf >= conf_thresh and area_px >= min_area_px:
                P.append((p_cls, p_conf, (pcx, pcy, pw, ph)))

        # Kiểm tra từng dự đoán
        for p_cls, p_conf, p_box in P:
            cands = []
            for line_no, l_cls, l_box in valid_labels:
                iou = iou_xywh(p_box, l_box)
                if iou >= iou_missing:
                    cands.append((line_no, l_cls, l_box, iou))

            if not cands:
                issues.append(Issue(
                    "second_opinion", WARN, "POSSIBLE_MISSING", stem,
                    f"model phát hiện {ds.classes[p_cls]} (conf={p_conf:.2f}) nhưng nhãn không có",
                    line=None, extra={"conf": p_conf}
                ))
            else:
                same_class_cands = [c for c in cands if c[1] == p_cls]
                if same_class_cands:
                    best_same_iou = max(c[3] for c in same_class_cands)
                    if best_same_iou < iou_loose:
                        issues.append(Issue(
                            "second_opinion", WARN, "POSSIBLE_LOOSE_BOX", stem,
                            f"nhãn {ds.classes[p_cls]} khớp với model nhưng box lỏng/lệch (IoU={best_same_iou:.2f} < {iou_loose})",
                            line=None, extra={"conf": p_conf, "iou": best_same_iou}
                        ))
                else:
                    best_diff_cand = max(cands, key=lambda c: c[3])
                    best_diff_iou = best_diff_cand[3]
                    diff_cls = best_diff_cand[1]
                    if best_diff_iou >= iou_match:
                        issues.append(Issue(
                            "second_opinion", WARN, "POSSIBLE_WRONG_CLASS", stem,
                            f"model đoán {ds.classes[p_cls]} (conf={p_conf:.2f}) nhưng nhãn là {ds.classes[diff_cls]} (IoU={best_diff_iou:.2f})",
                            line=None, extra={"conf": p_conf, "iou": best_diff_iou}
                        ))

        # Kiểm tra FALSE_POS cho từng dòng nhãn hợp lệ
        fp_preds = [(pr[0], pr[1], (pr[2], pr[3], pr[4], pr[5])) for pr in raw_preds if pr[1] >= fp_conf]
        for line_no, l_cls, l_box in valid_labels:
            matches_any = any(iou_xywh(l_box, p_box) >= iou_missing for _, _, p_box in fp_preds)
            if not matches_any:
                issues.append(Issue(
                    "second_opinion", WARN, "POSSIBLE_FALSE_POS", stem,
                    f"nhãn dòng {line_no} không khớp dự đoán nào của model",
                    line=line_no, extra={"conf": fp_conf}
                ))

    return issues
