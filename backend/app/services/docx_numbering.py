"""Recover visible Word automatic list labels without guessing from prose."""
import re
from lxml import etree
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
VAL = "{" + NS["w"] + "}val"

def numbered_paragraph_labels(document, numbering):
    result = {}
    if numbering is None:
        return result
    definitions = {n.get("{"+NS["w"]+"}abstractNumId"): n for n in numbering.findall("w:abstractNum", NS)}
    instances = {n.get("{"+NS["w"]+"}numId"): n for n in numbering.findall("w:num", NS)}
    counts = {}
    for paragraph in document.xpath(".//w:p", namespaces=NS):
        props = paragraph.find("w:pPr/w:numPr", NS)
        if props is None:
            continue
        num = props.find("w:numId", NS)
        level = props.find("w:ilvl", NS)
        if num is None:
            continue
        num_id = num.get(VAL)
        ilvl = int(level.get(VAL)) if level is not None else 0
        instance = instances.get(num_id)
        if instance is None:
            continue
        ref = instance.find("w:abstractNumId", NS)
        abstract = definitions.get(ref.get(VAL)) if ref is not None else None
        if abstract is None:
            continue
        levels = abstract.xpath('./w:lvl[@w:ilvl="'+str(ilvl)+'"]', namespaces=NS)
        overrides = instance.xpath('./w:lvlOverride[@w:ilvl="'+str(ilvl)+'"]', namespaces=NS)
        override = overrides[0] if overrides else None
        replacement = override.find('w:lvl', NS) if override is not None else None
        definition = replacement if replacement is not None else levels[0] if levels else None
        if definition is None:
            continue
        start = definition.find('w:start', NS)
        start_override = override.find('w:startOverride', NS) if override is not None else None
        initial = int(start_override.get(VAL)) if start_override is not None else int(start.get(VAL)) if start is not None else 1
        key = (num_id, ilvl)
        counts[key] = counts.get(key, initial - 1) + 1
        for deeper in list(counts):
            if deeper[0] == num_id and deeper[1] > ilvl:
                # Default Word behavior restarts nested lists after the parent.
                nested = abstract.xpath('./w:lvl[@w:ilvl="'+str(deeper[1])+'"]/w:lvlRestart', namespaces=NS)
                if not nested or nested[0].get(VAL) != '0':
                    del counts[deeper]
        fmt = definition.find('w:numFmt', NS)
        pattern = definition.find('w:lvlText', NS)
        if fmt is None or pattern is None:
            continue
        kind, template = fmt.get(VAL), pattern.get(VAL)
        # Support the simple decimal/alphabetic labels used by these exams.
        # Multilevel composite and bullet labels stay unexpanded for draft review.
        if template.count('%') != 1 or '%'+str(ilvl+1) not in template:
            continue
        value = counts[key]
        if kind == 'decimal':
            label = str(value)
        elif kind in ('upperLetter','lowerLetter') and 1 <= value <= 26:
            label = chr((65 if kind == 'upperLetter' else 97)+value-1)
        else:
            continue
        text = ''.join(paragraph.xpath('.//w:t/text()', namespaces=NS)).strip()
        visible = template.replace('%'+str(ilvl+1), label)
        if not re.match(r'^'+re.escape(visible)+r'(?:\s|$)', text):
            # Bare alphabetic list styles still need an unambiguous parser marker.
            result[paragraph] = ('['+label+']' if kind.endswith('Letter') else label+'.') + ' '
    return result
