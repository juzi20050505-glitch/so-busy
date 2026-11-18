// Synthetic browser evidence, exercised without website access.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
import {readJumengCard, compareJumengCard, expectedFromPackage} from './web_readback.mjs';

const url = 'https://example.invalid/work?seriesId=show&storyBoardId=card-a';
const expected = {
  url, card_index: 3, model: '九梦 2.5 Pro', mode: 'native', resolution: '480P',
  seconds: 12, format: 'MP4', body: '角色甲看向【@图片1】。\r\n他合上门。\r\n',
  references: [{name: '角色甲', marker: '【@图片1】'}],
};
const controls = [
  '- button "分镜 3，原生，12秒，已选中"',
  '- generic "九梦 2.5 Pro":', '  - combobox',
  '- button "全能参考" [pressed]',
  '- button "分辨率 480P，格式 MP4，时长 12 秒"',
].join('\n');

function element(tagName, children, classes = []) {
  return {nodeType: 1, tagName, childNodes: children, classList: {contains: x => classes.includes(x)},
    textContent: children.map(c => c.textContent).join(''), querySelector: () => null,
    getBoundingClientRect: () => ({width: 300})};
}
const text = textContent => ({nodeType: 3, textContent});
function editorFixture() {
  const mention = element('SPAN', [text('角色甲')], ['node-inline-mention']);
  return element('DIV', [
    element('P', [text('角色甲看向'), mention, text('。')]),
    element('P', [text('他合上门。'), element('BR', [], ['ProseMirror-trailingBreak'])]),
  ]);
}
async function sample({snapshot = controls, urls = [url, url], roots = [editorFixture()]} = {}) {
  const tab = {url: async () => urls.shift(), playwright: {
    domSnapshot: async () => Array.isArray(snapshot) ? snapshot.shift() : snapshot,
    evaluate: async fn => vm.runInNewContext(`(${fn.toString()})()`, {document: {querySelectorAll: () => roots}}),
  }};
  return readJumengCard(tab);
}
test('reads actual bound node in place, not identical plain text', async () => {
  const actual = await sample();
  assert.equal(compareJumengCard(actual, expected).status, 'matched');
  const moved = {...actual, editor: [{name: '角色甲'}, '看向角色甲。\n他合上门。']};
  assert.equal(compareJumengCard(moved, expected).status, 'mismatch');
  const plain = {...actual, editor: ['角色甲看向【@图片1】。\n他合上门。']};
  assert.equal(compareJumengCard(plain, expected).issues[0].field, 'bound_node_count');
});
test('supports preserved brackets and rejects changed text or reference identity', async () => {
  const actual = await sample();
  const editor = ['角色甲看向【', {name:'角色甲'}, '】。\n他合上门。'];
  assert.equal(compareJumengCard({...actual, editor}, expected).status, 'matched');
  for (const body of [expected.body.replace('门', '窗'), expected.body.replace('。\r\n他', '。\r\n\r\n他')]) {
    assert.equal(compareJumengCard(actual, {...expected, body}).status, 'mismatch');
  }
  assert.equal(compareJumengCard(actual, {...expected, references: [{name:'另一个角色', marker:'【@图片1】'}]}).status, 'mismatch');
});
test('blocks stale targets, unmatched duration and non-unique editor', async () => {
  const actual = await sample();
  assert.equal(compareJumengCard(actual, {...expected, seconds:15}).status, 'mismatch');
  assert.equal(compareJumengCard(actual, {...expected, card_index:2}).status, 'mismatch');
  const changed = await sample({urls:[url, url + '2']});
  assert.equal(compareJumengCard(changed, expected).status, 'needs_review');
  const ambiguous = await sample({roots:[editorFixture(), editorFixture()]});
  assert.equal(compareJumengCard(ambiguous, expected).status, 'needs_review');
  const episodeUrl = 'https://example.invalid/work?seriesId=show';
  assert.equal(compareJumengCard({...actual, url:episodeUrl}, {...expected, url:episodeUrl}).status, 'needs_review');
});
test('uncertain or missing visible fields are surfaced, even for valid adopted inputs', async () => {
  const snapshot = controls.replace('  - combobox', '  - combobox [expanded]')
    .replace('- button "全能参考" [pressed]', '- button "切换视频编辑或视频延长" [pressed]\n  - generic: 视频延长');
  const report = compareJumengCard(await sample({snapshot}), {...expected, mode:'extend', aspect_ratio:'9:16'});
  assert.equal(report.status, 'needs_review');
  assert.deepEqual(report.issues.map(x => x.field), ['model','aspect_ratio','source_result_and_interval']);
});
test('same display name without distinguishing thumbnails needs separate identity check', async () => {
  const observed = {...await sample(), editor:[{name:'同名'}, ' ', {name:'同名'}]};
  const references = [{name:'同名', marker:'甲'}, {name:'同名', marker:'乙'}];
  const report = compareJumengCard(observed, {...expected, body:'甲 乙', references});
  assert.equal(report.status, 'needs_review');
  assert.ok(report.issues.some(i => i.field === 'reference[1].identity'));
  observed.editor = [{name:'同名',thumbnail:'same.png'}, ' ', {name:'同名',thumbnail:'same.png'}];
  const sharedImage = references.map(ref => ({...ref, thumbnail:'same.png'}));
  assert.equal(compareJumengCard(observed, {...expected, body:'甲 乙', references:sharedImage}).status, 'needs_review');
});
test('a card can change while reading the editor without changing the URL', async () => {
  const actual = await sample({snapshot:[controls.replace('分镜 3，','分镜 2，'), controls]});
  const report = compareJumengCard(actual, expected);
  assert.equal(report.status, 'needs_review');
  assert.ok(report.issues.some(i => i.field === 'page_changed'));
});
test('derive web expectations from adopted bytes, including UTF-16 and explicit marker forms', async () => {
  const body = expected.body.replace('【@图片1】','{A}');
  const raw = Buffer.from('\uFEFF' + body, 'utf16le');
  const pkg = {version:1, references:['角色甲'], mode:'native', seconds:12,
    prompt_sha256:createHash('sha256').update(raw).digest('hex')};
  const target = {...expected, markers:['{A}'], references:['ignored'], seconds:1, mode:'extend'};
  const adopted = expectedFromPackage(pkg, raw, target);
  assert.equal(adopted.seconds, 12);
  assert.equal(adopted.mode, 'native');
  assert.equal(adopted.references[0].name, '角色甲');
  assert.equal(compareJumengCard(await sample(), adopted).status, 'matched');
  assert.throws(() => expectedFromPackage(pkg, Buffer.from(body), target), /SHA-256/);
  assert.throws(() => expectedFromPackage(pkg, raw, {...target, markers:['{B}']}), /missing/);
  const utf8 = Buffer.from(expected.body);
  pkg.prompt_sha256 = createHash('sha256').update(utf8).digest('hex');
  assert.equal(expectedFromPackage(pkg, utf8, expected).references[0].marker, '【@图片1】');
});
