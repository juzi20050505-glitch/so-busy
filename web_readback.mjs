// Same-page observation for the Jumeng browser adapter. These functions only read.
import {createHash} from 'node:crypto';

// Build expectations from the adopted bytes and package BEFORE inspecting live values.
export function expectedFromPackage(pkg, promptBytes, target) {
  if (pkg?.version !== 1 || !['native', 'extend'].includes(pkg.mode) ||
      !Number.isSafeInteger(pkg.seconds) || pkg.seconds <= 0 || !Array.isArray(pkg.references) ||
      !pkg.references.every(name => typeof name === 'string' && name.trim())) {
    throw new Error('Invalid adopted prompt package');
  }
  if (!(promptBytes instanceof Uint8Array) ||
      createHash('sha256').update(promptBytes).digest('hex') !== pkg.prompt_sha256) {
    throw new Error('Use the original bytes matching the adopted prompt SHA-256');
  }
  let encoding = 'utf-8';
  if (promptBytes[0] === 255 && promptBytes[1] === 254) encoding = 'utf-16le';
  if (promptBytes[0] === 254 && promptBytes[1] === 255) encoding = 'utf-16be';
  const body = new TextDecoder(encoding, {fatal: true}).decode(promptBytes);
  const markers = target.markers ?? body.match(/【@(?:图片|视频|音频)\d+】|@(?:图片|视频|音频)\d+/g) ?? [];
  if (!Array.isArray(markers) || markers.length !== pkg.references.length ||
      (target.thumbnails !== undefined &&
       (!Array.isArray(target.thumbnails) || target.thumbnails.length !== markers.length))) {
    throw new Error('Supply one adopted body marker per reference, in binding order');
  }
  let offset = 0;
  const references = pkg.references.map((name, index) => {
    const marker = markers[index];
    const start = typeof marker === 'string' && marker.trim() ? body.indexOf(marker, offset) : -1;
    if (start < 0) throw new Error('Reference marker missing or out of order in the adopted body');
    offset = start + marker.length;
    return {name, marker, thumbnail: target.thumbnails?.[index]};
  });
  const {url, card_index, model, resolution, aspect_ratio, format} = target;
  return {url, card_index, model, resolution, aspect_ratio, format, body, references,
    mode: pkg.mode, seconds: pkg.seconds};
}
function only(values) {
  return values.length === 1 ? values[0] : null;
}

function readControls(snapshot) {
  const lines = snapshot.split('\n');
  const params = only([...snapshot.matchAll(/^\s*- button "分辨率 ([^"，]+)，格式 ([^"，]+)，时长 (\d+) 秒"/gm)]);
  const selected = only([...snapshot.matchAll(/^\s*- button "分镜 (\d+)，[^"\n]+，已选中"/gm)]);
  const models = [];
  const modes = [];
  for (let i = 0; i < lines.length; i++) {
    const model = /^(\s*)- generic "((?:九梦|即梦)[^"]+)":?\s*$/.exec(lines[i]);
    if (model) {
      const next = lines[i + 1] ?? '';
      if (/^\s*- combobox\b/.test(next) && next.search(/\S/) > model[1].length &&
          !next.includes('[expanded]')) models.push(model[2]);
    }
    if (/^\s*- button "全能参考" \[pressed\]/.test(lines[i])) modes.push('native');
    if (/^\s*- button "切换视频编辑或视频延长" \[pressed\]/.test(lines[i])) {
      const next = lines[i + 1] ?? '';
      if (next.search(/\S/) > lines[i].search(/\S/)) {
        if (/^\s*- generic: 视频延长\s*$/.test(next)) modes.push('extend');
        if (/^\s*- generic: 视频编辑\s*$/.test(next)) modes.push('edit');
      }
    }
    if (/^\s*- button "首尾帧" \[pressed\]/.test(lines[i])) modes.push('first_last');
  }
  const ratio = only([...snapshot.matchAll(/^\s*- button "画幅[ ：:]+(\d+[:：]\d+)"/gm)]);
  return {
    card_index: selected ? Number(selected[1]) : null,
    mode: only(modes), model: only(models), resolution: params?.[1] ?? null,
    format: params?.[2] ?? null, seconds: params ? Number(params[3]) : null,
    aspect_ratio: ratio ? ratio[1].replace('：', ':') : null,
  };
}

// Passed to the host's read-only browser evaluator. Keep all DOM access inside this function.
function readEditor() {
  const editors = [...document.querySelectorAll('.tiptap')].filter(e => e.getBoundingClientRect().width > 0);
  if (editors.length !== 1) return null;
  const runs = [];
  function visit(node) {
    if (node.nodeType === 3) {
      runs.push(node.textContent);
    } else if (node.nodeType === 1) {
      if (node.classList.contains('node-inline-mention')) {
        runs.push({name: node.textContent.trim(), thumbnail: node.querySelector('img')?.getAttribute('src') ?? null});
      } else if (node.tagName === 'BR') {
        if (!node.classList.contains('ProseMirror-trailingBreak')) runs.push('\n');
      } else {
        for (const child of node.childNodes) visit(child);
      }
    }
  }
  for (const [i, paragraph] of Array.from(editors[0].childNodes).entries()) {
    if (i && paragraph.nodeType === 1 && /^(P|DIV)$/.test(paragraph.tagName)) runs.push('\n');
    visit(paragraph);
  }
  return runs;
}

export async function readJumengCard(tab) {
  const url = await tab.url();
  const before = readControls(await tab.playwright.domSnapshot());
  const editor = await tab.playwright.evaluate(readEditor);
  const controls = readControls(await tab.playwright.domSnapshot());
  const finalUrl = await tab.url();
  const stable = url === finalUrl && Object.keys(before).every(key => before[key] === controls[key]);
  return {url, stable, ...controls, editor};
}

// A terminal newline in a saved text file may be absent from the web editor.
function comparableText(text) {
  return text.replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').replace(/\n$/, '');
}

function cardUrl(value) {
  try {
    const url = new URL(value);
    url.searchParams.sort();
    return url;
  } catch {
    return null;
  }
}

export function compareJumengCard(actual, expected) {
  const issues = [];
  const checked = [];
  function issue(field, kind) { issues.push({field, kind}); }
  function compare(field, observed, wanted) {
    if (observed == null || wanted == null) issue(field, 'needs_review');
    else if (observed !== wanted) issue(field, 'mismatch');
    else checked.push(field);
  }
  const url = cardUrl(actual.url);
  const target = cardUrl(expected.url);
  compare('url', url?.href, target?.href);
  if (actual.stable !== true) issue('page_changed', 'needs_review');
  if (!['seriesId', 'storyBoardId'].every(key => url?.searchParams.getAll(key).length === 1 && url.searchParams.get(key))) {
    issue('card_identity', 'needs_review');
  }
  for (const field of ['card_index', 'mode', 'model', 'resolution', 'seconds']) {
    compare(field, actual[field], expected[field]);
  }
  for (const field of ['aspect_ratio', 'format']) {
    if (expected[field] !== undefined) compare(field, actual[field], expected[field]);
  }
  if (expected.mode === 'extend') issue('source_result_and_interval', 'needs_review');

  const refs = expected.references;
  if (!Array.isArray(actual.editor) || !Array.isArray(refs) || typeof expected.body !== 'string' ||
      !expected.body.trim() || !refs.every(r => typeof r?.name === 'string' && r.name.trim() &&
                                              typeof r.marker === 'string' && r.marker.trim())) {
    issue('editor_or_adopted_body', 'needs_review');
  } else {
    const nodes = actual.editor.filter(run => typeof run !== 'string');
    if (nodes.length !== refs.length) {
      issue('bound_node_count', 'mismatch');
    } else {
      const earlier = new Map();
      nodes.forEach((node, i) => {
        const ref = refs[i];
        compare(`reference[${i}].name`, node.name, ref.name);
        if (ref.thumbnail) compare(`reference[${i}].thumbnail`, node.thumbnail, ref.thumbnail);
        const sameName = earlier.get(ref.name) ?? [];
        if (sameName.some(prior => !ref.thumbnail || !prior.thumbnail ||
            (ref.marker !== prior.marker && ref.thumbnail === prior.thumbnail))) {
          issue(`reference[${i}].identity`, 'needs_review');
        }
        sameName.push(ref);
        earlier.set(ref.name, sameName);
      });
      // One UI path replaces the whole 【@图片N】, another replaces its inner text.
      // Rebuild at actual DOM-node boundaries, even when a name also occurs as plain text.
      const want = comparableText(expected.body);
      const matches = [false, true].some(inner => {
        let i = 0;
        const text = actual.editor.map(run => {
          if (typeof run === 'string') return run;
          const marker = refs[i++].marker;
          return inner ? marker.replace(/^【(.+)】$/, '$1') : marker;
        }).join('');
        return comparableText(text) === want;
      });
      if (matches) checked.push('body');
      else issue('body', 'mismatch');
    }
  }
  const status = issues.some(item => item.kind === 'mismatch') ? 'mismatch' :
    issues.length ? 'needs_review' : 'matched';
  return {status, checked, issues};
}
