/**
 * Lightweight markdown renderer (P1.D — spec §5 Zone 5).
 *
 * P1 coverage: h2 (## ), table (simple pipe syntax), **bold**,
 * paragraphs separated by empty lines. No h1/h3, lists,
 * links, code blocks — not in P1 scope.
 *
 * No external dependency. Naive renderer that walks the lines
 * and accumulates blocks. Sufficient for the short-notes cases in the brief.
 */
import { useTranslation } from 'react-i18next';

export default function MarkdownPreview({ source }) {
  const { t } = useTranslation();
  if (!source || !source.trim()) {
    return (
      <p className="text-xs2 text-text-faint italic">
        {t('papers.notes_empty')}
      </p>
    );
  }
  const blocks = _parseBlocks(source);
  return (
    <div className="space-y-2.5 text-[12.5px] leading-[1.55] text-text-strong">
      {blocks.map((b, i) => _renderBlock(b, i))}
    </div>
  );
}


function _parseBlocks(src) {
  const lines = src.split('\n');
  const blocks = [];
  let current = null;  // { type, lines }

  const flush = () => {
    if (current) {
      blocks.push(current);
      current = null;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('## ')) {
      flush();
      blocks.push({ type: 'h2', text: trimmed.slice(3).trim() });
      continue;
    }
    // Table: line starting with |, and the next line is a separator
    // |---|---| (the separator check lets us distinguish from a plain line
    // with a pipe). We accumulate in 'table' mode as long as we stay on |.
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      if (!current || current.type !== 'table') {
        flush();
        current = { type: 'table', lines: [] };
      }
      current.lines.push(trimmed);
      continue;
    }
    if (trimmed === '') {
      flush();
      continue;
    }
    if (!current || current.type !== 'p') {
      flush();
      current = { type: 'p', lines: [] };
    }
    current.lines.push(trimmed);
  }
  flush();
  return blocks;
}


function _renderBlock(block, key) {
  if (block.type === 'h2') {
    return (
      <h3 key={key} className="text-sm font-semibold text-text-strong mt-3 first:mt-0">
        {_renderInline(block.text)}
      </h3>
    );
  }
  if (block.type === 'p') {
    return (
      <p key={key} className="text-[12.5px] leading-[1.55]">
        {_renderInline(block.lines.join(' '))}
      </p>
    );
  }
  if (block.type === 'table') {
    return _renderTable(block.lines, key);
  }
  return null;
}


function _renderTable(lines, key) {
  // 1st line = header, 2nd = separator (|---|---|), 3+ = rows
  const cells = lines.map((l) =>
    l.slice(1, -1).split('|').map((c) => c.trim())
  );
  if (cells.length < 2) {
    // Not a real table, render as a paragraph fallback
    return <p key={key} className="text-[12.5px]">{lines.join(' ')}</p>;
  }
  const header = cells[0];
  // Skip line 2 if it's a separator (---)
  const dataStart = cells[1].every((c) => /^[-:]+$/.test(c)) ? 2 : 1;
  const rows = cells.slice(dataStart);
  return (
    <div key={key} className="overflow-x-auto">
      <table className="text-tiny w-full border-collapse">
        <thead>
          <tr>
            {header.map((h, i) => (
              <th key={i} className="text-left font-medium text-text-muted border-b border-border-soft px-2 py-1">
                {_renderInline(h)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((c, ci) => (
                <td key={ci} className="border-b border-border-soft/40 px-2 py-1 align-top">
                  {_renderInline(c)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


/**
 * Inline rendering: **bold** (regex), rest plain. No italics in P1.
 * Returns an array of React elements.
 */
function _renderInline(text) {
  const parts = [];
  let lastIdx = 0;
  // Match **bold**
  const re = /\*\*([^*]+)\*\*/g;
  let m;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIdx) {
      parts.push(text.slice(lastIdx, m.index));
    }
    parts.push(<strong key={i++}>{m[1]}</strong>);
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < text.length) {
    parts.push(text.slice(lastIdx));
  }
  return parts;
}
