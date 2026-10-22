"""Write docs/SCHEMA_CARD.md, the database description given to the LLM."""
from sqlhelper.policy import ROOT
from sqlhelper.schema import build_card

OUT = ROOT / "docs" / "SCHEMA_CARD.md"


def main() -> None:
    card = build_card()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(card)
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(card):,} characters, ~{len(card) // 4:,} tokens)")


if __name__ == "__main__":
    main()
