import unittest
import tempfile
import zipfile
from pathlib import Path
from lxml import etree
from backend.app.services.docx_numbering import numbered_paragraph_labels
from backend.app.services.docx_parser import extract_blocks, _parse_reading, _parse_part_b

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
def xml(body): return etree.fromstring(body.encode())

class NumberingRemediationTests(unittest.TestCase):
    def test_start_override_and_nested_restart(self):
        numbering=xml(f'''<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0">
          <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
          <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="upperLetter"/><w:lvlText w:val="[%2]"/></w:lvl>
        </w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="21"/></w:lvlOverride></w:num></w:numbering>''')
        paragraphs=''.join(f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>content</w:t></w:r></w:p>' for level in [0,1,1,0,1])
        doc=xml(f'<w:document xmlns:w="{W}"><w:body>{paragraphs}</w:body></w:document>')
        self.assertEqual(list(numbered_paragraph_labels(doc,numbering).values()),['21. ','[A] ','[B] ','22. ','[A] '])

    def test_table_paragraphs_keep_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'table.docx'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('[Content_Types].xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml')
                z.writestr('word/document.xml',f'<w:document xmlns:w="{W}"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>[A] first statement</w:t></w:r></w:p><w:p><w:r><w:t>41. First person</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
            blocks,_,_=extract_blocks(path)
            self.assertEqual(blocks,['[A] first statement\n41. First person'])

    def test_combined_part_b_header_bounds_reading_and_recovers_names(self):
        blocks=['Text 4','Reading body.']
        for n in range(36,41):
            blocks += [f'{n}. What does L.A. policy require?', '[A] first','[B] second','[C] third','[D] fourth']
        blocks += ['Part B Directions:', 'Match the left column to the right column. There are two extra choices.', 'Source body.',
                   '[A] First opinion\n41. Alice\n[B] Second opinion\n42. Bob\n[C] Third opinion\n43. Carol\n[D] Fourth opinion\n44. Dan\n[E] Fifth opinion\n45. Eve\n[F] Sixth opinion\n[G] Last opinion\ncontinues here.', '46. Directions:', 'Section IIITranslation']
        reading=_parse_reading(blocks,{})[0]
        self.assertEqual(len(reading['questions']),5)
        self.assertEqual(reading['questions'][0]['stem'],'What does L.A. policy require?')
        self.assertTrue(all(len(q['options'])==4 for q in reading['questions']))
        part=_parse_part_b(blocks,{})
        self.assertEqual([q['stem'] for q in part['questions']], ['Alice','Bob','Carol','Dan','Eve'])
        self.assertEqual(part['shared_data']['candidates']['G'],'Last opinion continues here.')
        self.assertEqual(part['passage'],'Source body.')
