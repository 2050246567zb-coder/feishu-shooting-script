"""Read DOCX content as data; preserve table coordinates and merged-cell origins."""
import argparse
import json
import posixpath
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'v': 'urn:schemas-microsoft-com:vml',
}
W, R = '{'+NS['w']+'}', '{'+NS['r']+'}'


def node_text(node):
    if node.tag == W+'del':
        return ''
    if node.tag == W+'t':
        return node.text or ''
    if node.tag in (W+'br', W+'cr'):
        return '\n'
    if node.tag == W+'tab':
        return '\t'
    return ''.join(node_text(child) for child in node)


def extract(source, output_dir):
    source, output_dir = Path(source).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    media_dir = output_dir/'media'
    with zipfile.ZipFile(source) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        rels = {}
        if 'word/_rels/document.xml.rels' in archive.namelist():
            rels = {r.attrib['Id']: r.attrib for r in ET.fromstring(archive.read('word/_rels/document.xml.rels'))}
        media = {}
        for rid, rel in rels.items():
            if not rel.get('Type', '').endswith('/image') or rel.get('TargetMode') == 'External':
                continue
            entry = posixpath.normpath(posixpath.join('word', rel['Target']))
            if entry not in archive.namelist():
                continue
            media_dir.mkdir(exist_ok=True)
            # No zip entry path is used as a destination path.
            safe_rid = ''.join(c for c in rid if c.isalnum())
            destination = media_dir/(safe_rid+'-'+posixpath.basename(entry))
            destination.write_bytes(archive.read(entry))
            media[rid] = str(destination.relative_to(output_dir))

        def content(node):
            paragraphs = [node_text(node)] if node.tag == W+'p' else [node_text(p) for p in node.findall('./w:p', NS)]
            image_ids = [b.attrib.get(R+'embed') for b in node.findall('.//a:blip', NS)]
            image_ids += [b.attrib.get(R+'id') for b in node.findall('.//v:imagedata', NS)]
            links = []
            for link in node.findall('.//w:hyperlink', NS):
                target = rels.get(link.attrib.get(R+'id'), {}).get('Target')
                if target:
                    links.append({'text': node_text(link), 'url': target})
            return {'text': '\n'.join(paragraphs), 'images': [media[r] for r in image_ids if r in media], 'hyperlinks': links}

        blocks, warnings = [], []
        for bi, node in enumerate(root.find('w:body', NS)):
            if node.tag == W+'p':
                blocks.append({'block': bi, 'type': 'paragraph', **content(node)})
            elif node.tag == W+'tbl':
                rows, active_merges = [], {}
                for ri, row in enumerate(node.findall('./w:tr', NS)):
                    before = row.find('w:trPr/w:gridBefore', NS)
                    column = int(before.attrib.get(W+'val', 0)) if before is not None else 0
                    cells, next_merges = [], {}
                    for ci, cell in enumerate(row.findall('./w:tc', NS)):
                        span = cell.find('w:tcPr/w:gridSpan', NS)
                        span = int(span.attrib.get(W+'val', 1)) if span is not None else 1
                        merge = cell.find('w:tcPr/w:vMerge', NS)
                        merge = None if merge is None else merge.attrib.get(W+'val', 'continue')
                        origin = {'block': bi, 'row': ri, 'cell': ci}
                        item = {'cell': ci, 'column': column, 'colspan': span, 'vertical_merge': merge, **content(cell)}
                        if merge == 'continue':
                            origin = active_merges.get(column)
                            item['merged_from'] = origin
                            if origin is None:
                                warnings.append(f'Unresolved vertical merge at table {bi}, row {ri}, cell {ci}')
                        if merge and origin:
                            for col in range(column, column+span):
                                next_merges[col] = origin
                        if cell.find('./w:tbl', NS) is not None:
                            warnings.append(f'Nested table at table {bi}, row {ri}, cell {ci}; inspect with a document reader')
                        cells.append(item)
                        column += span
                    active_merges = next_merges
                    rows.append({'row': ri, 'cells': cells})
                blocks.append({'block': bi, 'type': 'table', 'rows': rows})
        if root.findall('.//w:txbxContent', NS):
            warnings.append('Floating text boxes found; supplement extraction with a document reader')
        comments = []
        if 'word/comments.xml' in archive.namelist():
            for comment in ET.fromstring(archive.read('word/comments.xml')):
                comments.append({'id': comment.attrib.get(W+'id'), 'text': '\n'.join(node_text(p) for p in comment.findall('./w:p', NS))})
        result = {'source_name': source.name, 'blocks': blocks, 'images': media, 'comments': comments, 'warnings': warnings}
    (output_dir/'extracted.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = []
    for block in blocks:
        if block['type'] == 'paragraph':
            if block['text'] or block['images']:
                lines.append(f"P{block['block']}: {block['text']}")
                if block['images']:
                    lines.append('  IMAGES '+json.dumps(block['images'], ensure_ascii=False))
        else:
            lines.append(f"TABLE {block['block']}")
            for row in block['rows']:
                for cell in row['cells']:
                    lines.append(f"R{row['row']} C{cell['cell']} span={cell['colspan']} merge={cell['vertical_merge']}: {cell['text']}")
                    if cell.get('merged_from'):
                        lines.append('  MERGED_FROM '+json.dumps(cell['merged_from']))
                    if cell['images']:
                        lines.append('  IMAGES '+json.dumps(cell['images'], ensure_ascii=False))
    (output_dir/'extracted.txt').write_text('\n'.join(lines), encoding='utf-8')
    return {'tables': sum(b['type']=='table' for b in blocks), 'images': len(media), 'warnings': warnings, 'output_dir': str(output_dir)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    print(json.dumps(extract(args.source, args.output_dir), ensure_ascii=False, indent=2))
