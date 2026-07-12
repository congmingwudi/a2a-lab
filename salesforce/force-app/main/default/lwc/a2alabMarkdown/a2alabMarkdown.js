/**
 * Minimal, safe markdown renderer shared by the A2ALab brief components:
 * input is HTML-escaped FIRST, then only our own tags are injected
 * (headings, bold/italic, links, lists, paragraphs).
 * lightning-formatted-rich-text sanitizes the result again on render.
 */
function escapeHtml(s) {
    return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);
}

function inlineMd(s) {
    return s
        .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
        .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
        .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<i>$2</i>');
}

export function mdToHtml(src) {
    // Web-search citation markers occasionally leak into saved briefs —
    // strip them rather than render them as literal text.
    src = String(src).replace(/<\/?cite[^>]*>/g, '');
    const lines = escapeHtml(src).split(/\r?\n/);
    const out = [];
    let list = null;
    let para = [];
    const flushPara = () => {
        if (para.length) {
            out.push('<p>' + para.map(inlineMd).join('<br/>') + '</p>');
            para = [];
        }
    };
    const closeList = () => {
        if (list) {
            out.push('</' + list + '>');
            list = null;
        }
    };
    for (const line of lines) {
        const h = line.match(/^(#{1,4})\s+(.*)/);
        const ul = line.match(/^\s*[-*]\s+(.*)/);
        const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
        if (h) {
            flushPara();
            closeList();
            const lvl = Math.min(h[1].length + 2, 6);
            out.push('<h' + lvl + '>' + inlineMd(h[2]) + '</h' + lvl + '>');
        } else if (ul) {
            flushPara();
            if (list !== 'ul') {
                closeList();
                out.push('<ul>');
                list = 'ul';
            }
            out.push('<li>' + inlineMd(ul[1]) + '</li>');
        } else if (ol) {
            flushPara();
            if (list !== 'ol') {
                closeList();
                out.push('<ol>');
                list = 'ol';
            }
            out.push('<li>' + inlineMd(ol[1]) + '</li>');
        } else if (!line.trim()) {
            flushPara();
            closeList();
        } else {
            para.push(line);
        }
    }
    flushPara();
    closeList();
    return out.join('');
}
