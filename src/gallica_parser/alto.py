from __future__ import annotations

import re
import xml.etree.ElementTree as ET


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_alto_text(xml_data: bytes) -> tuple[str, str]:
    """Возвращает текст с сохранением строк и очищенную однострочную версию."""
    root = ET.fromstring(xml_data)
    raw_lines: list[str] = []
    clean_tokens: list[str] = []

    for line in (node for node in root.iter() if _local_name(node.tag) == "TextLine"):
        line_tokens: list[str] = []
        for token in (node for node in line if _local_name(node.tag) == "String"):
            content = token.attrib.get("CONTENT", "")
            if not content:
                continue
            line_tokens.append(content)

            subs_type = token.attrib.get("SUBS_TYPE")
            if subs_type == "HypPart2":
                continue
            if subs_type == "HypPart1" and token.attrib.get("SUBS_CONTENT"):
                clean_tokens.append(token.attrib["SUBS_CONTENT"])
            else:
                clean_tokens.append(content)
        if line_tokens:
            raw_lines.append(" ".join(line_tokens))

    raw = "\n".join(raw_lines)
    clean = " ".join(clean_tokens)
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r"(['’])\s+", r"\1", clean)
    clean = re.sub(r"\(\s+", "(", clean)
    clean = re.sub(r"\s+\)", ")", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return raw, clean

