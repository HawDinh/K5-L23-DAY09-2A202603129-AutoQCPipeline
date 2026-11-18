"""CHECK 2 — Dòng nhãn YOLO: sai định dạng, ID lớp lạ, box tràn ảnh, box rỗng, box lặp.   (Script 2 trong slide)   ★★

Test:  pytest tests/test_label_format.py -q

Mỗi dòng nhãn: `id cx cy w h` (5 số, toạ độ 0–1). Trả về list[Issue]; `Issue.line` = số dòng (đếm từ 1).

| code             | severity | khi nào                                                                  |
|------------------|----------|--------------------------------------------------------------------------|
| BAD_FORMAT       | ERROR    | không đúng 5 cột số (dùng `parse_yolo_line`, bắt `ValueError`)           |
| BAD_CLASS_ID     | ERROR    | id lớp ngoài 0..len(ds.classes)-1                                        |
| BOX_ZERO         | ERROR    | w <= 0 hoặc h <= 0                                                       |
| BOX_OUT_OF_IMAGE | ERROR    | mép box vượt [0,1] quá `cfg["label_format"]["overflow_tol"]`; extra={"overflow": mức tràn} |
| DUP_BOX          | ERROR    | cùng lớp với một dòng TRƯỚC ĐÓ và IoU > `dup_box_iou`; báo ở dòng SAU    |
| BOX_TINY         | WARN     | cạnh ngắn của box (px) < `min_box_px`  (cần `ds.image_size(stem)`)       |
| BOX_HUGE         | WARN     | w*h > `max_box_frac`                                                     |
"""
from ..common import ERROR, WARN, Issue, iou_xywh, parse_yolo_line


def run(ds, cfg):
    issues = []
    fmt_cfg = cfg.get("label_format", {})
    overflow_tol = fmt_cfg.get("overflow_tol", 0.002)
    min_box_px = fmt_cfg.get("min_box_px", 12)
    max_box_frac = fmt_cfg.get("max_box_frac", 0.95)
    dup_box_iou = fmt_cfg.get("dup_box_iou", 0.9)

    for p in ds.label_files():
        stem = p.stem
        lines = ds.read_label_lines(stem)
        if lines is None:
            continue

        size = ds.image_size(stem)
        prev_boxes = []  # [(line_no, class_id, (cx, cy, w, h))]

        for line_no, text in lines:
            try:
                c, cx, cy, w, h = parse_yolo_line(text)
            except ValueError:
                issues.append(Issue("label_format", ERROR, "BAD_FORMAT", stem, "định dạng không hợp lệ", line=line_no))
                continue

            if not (0 <= c < len(ds.classes)):
                issues.append(Issue("label_format", ERROR, "BAD_CLASS_ID", stem, f"id lớp {c} ngoài 0..{len(ds.classes)-1}", line=line_no))
                continue

            if w <= 0 or h <= 0:
                issues.append(Issue("label_format", ERROR, "BOX_ZERO", stem, f"kích thước không dương (w={w}, h={h})", line=line_no))
                continue

            # Kiểm tra tràn ảnh
            x1 = cx - w / 2
            y1 = cy - h / 2
            x2 = cx + w / 2
            y2 = cy + h / 2
            over = max(0.0, -x1, -y1, x2 - 1.0, y2 - 1.0)
            if over > overflow_tol:
                issues.append(Issue("label_format", ERROR, "BOX_OUT_OF_IMAGE", stem, f"box tràn ảnh {over:.4f} > {overflow_tol}", line=line_no, extra={"overflow": over}))

            # Kiểm tra trùng box cùng lớp
            curr_box = (cx, cy, w, h)
            for prev_line_no, prev_c, prev_box in prev_boxes:
                if prev_c == c:
                    iou = iou_xywh(curr_box, prev_box)
                    if iou > dup_box_iou:
                        issues.append(Issue("label_format", ERROR, "DUP_BOX", stem, f"trùng box với dòng {prev_line_no} (IoU={iou:.3f})", line=line_no))
                        break

            prev_boxes.append((line_no, c, curr_box))

            # Kiểm tra box quá nhỏ
            if size is not None:
                img_w, img_h = size
                short_side_px = min(w * img_w, h * img_h)
                if short_side_px < min_box_px:
                    issues.append(Issue("label_format", WARN, "BOX_TINY", stem, f"cạnh ngắn {short_side_px:.1f}px < {min_box_px}px", line=line_no))

            # Kiểm tra box quá lớn
            if w * h > max_box_frac:
                issues.append(Issue("label_format", WARN, "BOX_HUGE", stem, f"diện tích {w*h:.2f} > {max_box_frac}", line=line_no))

    return issues
