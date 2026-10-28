"""校验四语 JSON 目录；无需编译，只读且仅使用标准库。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("zh_CN", "en_US", "en_GB", "zh_TW")


def catalog_entries(pairs: list[tuple[str, object]]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for key, value in pairs:
        if key in catalog:
            raise ValueError(f"duplicate translation key: {key!r}")
        if not key or not isinstance(value, str) or not value:
            raise ValueError(f"expected nonempty string translation: {key!r}")
        catalog[key] = value
    return catalog


def check_catalogs(root: Path) -> None:
    reference: set[str] | None = None
    for lang in LANGUAGES:
        path = root / f"{lang}.json"
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=catalog_entries)
            if not isinstance(catalog, dict) or not catalog:
                raise ValueError("expected a nonempty translation object")
            keys = set(catalog)
            if reference is None:
                reference = keys
            elif keys != reference:
                raise ValueError(
                    f"missing keys: {sorted(reference - keys)!r}; extra keys: {sorted(keys - reference)!r}"
                )
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path}: {exc}") from exc


def main() -> int:
    try:
        check_catalogs(ROOT / "i18n")
    except ValueError as exc:
        print(exc)
        return 1
    print("i18n JSON catalogs OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
