"""BƯỚC 2/4 — Sửa tự động những lỗi bắt buộc mà máy sửa được AN TOÀN.   ★★

Test:  pytest tests/test_autofix.py -q

`fix(ds, issues, cfg) -> list[dict]`: duyệt `issues` (chỉ lỗi severity ERROR), sửa thẳng vào thư mục `ds`,
và trả về danh sách thay đổi; mỗi phần tử có khoá: image, line, action, before, after
(đây chính là file `03_fixes.csv` — bằng chứng pipeline đã làm gì, người có thể kiểm lại).

| code lỗi                                       | cách sửa                                    | action           |
|------------------------------------------------|---------------------------------------------|------------------|
| BAD_FORMAT, BAD_CLASS_ID, BOX_ZERO, DUP_BOX    | xoá dòng đó khỏi file nhãn                  | "drop_line"      |
| BOX_OUT_OF_IMAGE                               | cắt box về trong [0,1] (giữ nguyên lớp)     | "clip_box"       |
| MISSING_LABEL_FILE                             | KHÔNG sửa file (máy không thể bịa nhãn): ghi 1 dòng fix, before="(không có file)", after="chuyển người gán" | "needs_label" |
| BAD_CLASS_NAME                                 | ghi lại `names` trong data.yaml đúng `cfg["classes"]` | "rename_classes" |
| DUP_IMAGE_*, LEAK_TEST, CORRUPT_IMAGE, BAD_SIZE| xoá ảnh + nhãn khỏi `ds`                    | "remove_image"   |
"""
from collections import defaultdict
from pathlib import Path

import yaml

from ..common import ERROR, ROOT, parse_yolo_line

DROP_CODES = {"BAD_FORMAT", "BAD_CLASS_ID", "BOX_ZERO", "DUP_BOX"}
REMOVE_IMAGE_CODES = {"LEAK_TEST", "CORRUPT_IMAGE", "BAD_SIZE"}


def fix(ds, issues, cfg):
    raw_dir = (ROOT / "data" / "raw").resolve()
    if ds.root.resolve() == raw_dir or raw_dir in ds.root.resolve().parents:
        raise ValueError("autofix không được sửa data/raw")

    err_issues = [i for i in issues if i.severity == ERROR]
    fixes = []
    removed_images = set()

    # 1. remove_image: DUP_IMAGE_*, LEAK_TEST, CORRUPT_IMAGE, BAD_SIZE
    for iss in err_issues:
        if iss.code.startswith("DUP_IMAGE_") or iss.code in REMOVE_IMAGE_CODES:
            stem = iss.image
            if stem not in removed_images:
                removed_images.add(stem)
                img_p = ds.image_path(stem)
                lbl_p = ds.label_path(stem)
                if not ((img_p and img_p.exists()) or (lbl_p and lbl_p.exists())):
                    continue
                if img_p and img_p.exists():
                    img_p.unlink()
                if lbl_p and lbl_p.exists():
                    lbl_p.unlink()
                fixes.append({
                    "image": stem,
                    "line": "",
                    "action": "remove_image",
                    "before": "image+label",
                    "after": "removed",
                })

    # 2. rename_classes: BAD_CLASS_NAME
    has_bad_class_name = any(iss.code == "BAD_CLASS_NAME" for iss in err_issues)
    if has_bad_class_name and ds.yaml_path.exists():
        try:
            data = yaml.safe_load(ds.yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        before = str(data.get("names", ""))
        if data.get("names") != list(cfg["classes"]):
            data["names"] = list(cfg["classes"])
            ds.yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            fixes.append({
                "image": "data.yaml",
                "line": "",
                "action": "rename_classes",
                "before": before,
                "after": str(cfg["classes"]),
            })

    # 3. needs_label: MISSING_LABEL_FILE
    needs_label_seen = set()
    for iss in err_issues:
        if iss.code == "MISSING_LABEL_FILE" and iss.image not in removed_images:
            if iss.image not in needs_label_seen:
                needs_label_seen.add(iss.image)
                fixes.append({
                    "image": iss.image,
                    "line": "",
                    "action": "needs_label",
                    "before": "(không có file)",
                    "after": "chuyển người gán",
                })

    # 4. Line-level fixes: drop_line, clip_box
    img_line_issues = defaultdict(lambda: defaultdict(list))
    for iss in err_issues:
        if iss.line is not None and iss.image not in removed_images:
            img_line_issues[iss.image][iss.line].append(iss)

    for stem in sorted(img_line_issues.keys()):
        lbl_p = ds.label_path(stem)
        if not lbl_p.exists():
            continue

        raw_lines = lbl_p.read_text(encoding="utf-8", errors="replace").splitlines()
        line_actions = {}  # line_no -> (action, before, after)

        for line_no, issues_at_line in sorted(img_line_issues[stem].items()):
            idx = line_no - 1
            if idx < 0 or idx >= len(raw_lines):
                continue
            orig_text = raw_lines[idx]

            has_drop = any(i.code in DROP_CODES for i in issues_at_line)
            has_overflow = any(i.code == "BOX_OUT_OF_IMAGE" for i in issues_at_line)

            if has_drop:
                line_actions[line_no] = ("drop_line", orig_text, "")
            elif has_overflow:
                try:
                    c, cx, cy, w, h = parse_yolo_line(orig_text)
                    x1 = cx - w / 2
                    y1 = cy - h / 2
                    x2 = cx + w / 2
                    y2 = cy + h / 2
                    cx1 = max(0.0, min(1.0, x1))
                    cx2 = max(0.0, min(1.0, x2))
                    cy1 = max(0.0, min(1.0, y1))
                    cy2 = max(0.0, min(1.0, y2))
                    nw = cx2 - cx1
                    nh = cy2 - cy1
                    if nw <= 0.0 or nh <= 0.0:
                        line_actions[line_no] = ("drop_line", orig_text, "")
                    else:
                        ncx = (cx1 + cx2) / 2
                        ncy = (cy1 + cy2) / 2
                        new_text = f"{c} {ncx:.6f} {ncy:.6f} {nw:.6f} {nh:.6f}"
                        line_actions[line_no] = ("clip_box", orig_text, new_text)
                except ValueError:
                    line_actions[line_no] = ("drop_line", orig_text, "")

        new_lines = []
        for idx, text in enumerate(raw_lines):
            lno = idx + 1
            if lno in line_actions:
                act, before, after = line_actions[lno]
                fixes.append({
                    "image": stem,
                    "line": lno,
                    "action": act,
                    "before": before,
                    "after": after,
                })
                if act == "clip_box":
                    new_lines.append(after)
            else:
                new_lines.append(text)

        lbl_p.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")

    return fixes
