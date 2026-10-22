import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { deflateSync } from 'node:zlib';
import test from 'node:test';
import { collectImages, inspectPng, jsonBytes, loadWorkspace, portableRelative, sha256, validateScript } from '../scripts/common.mjs';
import { prepare } from '../scripts/prepare_prompts.mjs';
import { recordImage } from '../scripts/record_image.mjs';
import { assemble, createImagePresentation } from '../scripts/assemble_deck.mjs';
import { createHandoff } from '../scripts/create_handoff.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
function inputScript() {
  return { schema_version: '1.0', title: 'Offline synthetic fixture', canvas: { width: 16, height: 9 }, extra: { preserve: true }, slides: [
    { id: 'P01', title: 'Fixture page', key_message: 'Test data only', on_slide_text: ['Test'], speaker_notes: '', image_prompt: 'Synthetic unit test', source_refs: ['synthetic:test'] },
  ] };
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) { crc ^= byte; for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const bytes = Buffer.alloc(data.length + 12); bytes.writeUInt32BE(data.length, 0); bytes.write(type, 4); data.copy(bytes, 8);
  bytes.writeUInt32BE(crc32(bytes.subarray(4, -4)), bytes.length - 4); return bytes;
}
function png(width = 16, height = 9) {
  const header = Buffer.alloc(13); header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  const rows = Buffer.alloc((width * 3 + 1) * height, 255);
  for (let row = 0; row < height; row++) rows[row * (width * 3 + 1)] = 0;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
}
async function fixture(t, script = inputScript()) {
  const root = await fs.mkdtemp(path.join(here, '.offline-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const source = path.join(root, 'source.json'); await fs.writeFile(source, jsonBytes(script));
  const workspace = path.join(root, 'prepared');
  return { root, source, workspace, script };
}
async function readyFixture(t, image = png()) {
  const f = await fixture(t); await prepare({ script: f.source, out: f.workspace });
  const imagePath = path.join(f.root, 'synthetic.png'); await fs.writeFile(imagePath, image);
  await recordImage({ workspace: f.workspace, id: 'P01', image: imagePath, 'source-origin': 'SYNTHETIC OFFLINE TEST: no model invoked', 'tool-result-ref': 'offline-test-fixture' });
  return { ...f, imagePath };
}
async function syntheticBuild(f) {
  const workspace = await loadWorkspace(f.workspace); const images = await collectImages(workspace);
  const build = path.join(f.root, 'synthetic-build'); await fs.mkdir(build);
  // A copy-integrity fixture, deliberately not a valid PPTX or a runtime export.
  const bytes = Buffer.from('OFFLINE COPY TEST ONLY — not a PowerPoint presentation');
  await fs.writeFile(path.join(build, 'deck.pptx'), bytes);
  await fs.writeFile(path.join(build, 'build-manifest.json'), jsonBytes({
    schema_version: '1.0', kind: 'assembled_image_deck', script_sha256: workspace.manifest.script_sha256,
    deck_path: 'deck.pptx', deck_sha256: sha256(bytes), slides: images.map(({ bytes: _bytes, ...record }) => record), qa: { powerpoint_gui: 'pending' },
  }));
  return build;
}

test('preparation preserves source bytes, extra fields and prompts', async t => {
  const f = await fixture(t); const before = await fs.readFile(f.source);
  await prepare({ script: f.source, out: f.workspace });
  assert.deepEqual(await fs.readFile(f.source), before);
  assert.deepEqual(await fs.readFile(path.join(f.workspace, 'script.json')), before);
  assert.equal((await loadWorkspace(f.workspace)).script.extra.preserve, true);
});
test('contract accepts empty text/source arrays and empty notes', () => {
  const script = inputScript(); script.slides[0].on_slide_text = []; script.slides[0].source_refs = [];
  assert.equal(validateScript(script), script);
});
test('duplicate and case-colliding ids fail before creating output', async t => {
  const script = inputScript(); script.slides.push({ ...script.slides[0], id: 'p01' });
  const f = await fixture(t, script);
  await assert.rejects(prepare({ script: f.source, out: f.workspace }), /duplicate/);
  await assert.rejects(fs.lstat(f.workspace), { code: 'ENOENT' });
});
test('path traversal and nonportable manifest paths are rejected', async t => {
  for (const value of ['../outside.png', '/tmp/x', 'C:\\x', 'assets/../x', 'assets//x', 'assets/./x']) assert.throws(() => portableRelative(value), /unsafe/);
  const f = await fixture(t); await prepare({ script: f.source, out: f.workspace });
  const filename = path.join(f.workspace, 'manifest.json'); const manifest = JSON.parse(await fs.readFile(filename));
  manifest.slides[0].image_path = '../outside.png'; await fs.writeFile(filename, jsonBytes(manifest));
  await assert.rejects(loadWorkspace(f.workspace), /unsafe/);
});
test('unsafe slide ids cannot create paths', () => {
  for (const id of ['../p', '/p', 'a/b', 'a\\b', '', 'a'.repeat(65), 12]) {
    const script = inputScript(); script.slides[0].id = id; assert.throws(() => validateScript(script), /slide id/);
  }
});
test('existing preparation output is preserved', async t => {
  const f = await fixture(t); await fs.mkdir(f.workspace); const sentinel = path.join(f.workspace, 'keep.txt'); await fs.writeFile(sentinel, 'KEEP');
  await assert.rejects(prepare({ script: f.source, out: f.workspace }), /already exists/);
  assert.equal(await fs.readFile(sentinel, 'utf8'), 'KEEP');
});
test('image registration preserves original bytes and records unknown model', async t => {
  const f = await readyFixture(t); const workspace = await loadWorkspace(f.workspace); const images = await collectImages(workspace);
  assert.equal(images[0].generation.model_id, 'unknown');
  assert.deepEqual(await fs.readFile(f.imagePath), images[0].bytes);
  await assert.rejects(recordImage({ workspace: f.workspace, id: 'P01', image: f.imagePath, 'source-origin': 'test', 'tool-result-ref': 'test' }), /already exists/);
});
test('missing image stops assembly before runtime use', async t => {
  const f = await fixture(t); await prepare({ script: f.source, out: f.workspace });
  await assert.rejects(assemble({ workspace: f.workspace, out: path.join(f.root, 'build') }), /ENOENT/);
});
test('existing assembly output stops before runtime use', async t => {
  const f = await readyFixture(t); const output = path.join(f.root, 'build'); await fs.mkdir(output);
  await assert.rejects(assemble({ workspace: f.workspace, out: output }), /already exists/);
});
test('missing runtime authorization stops without importing a module', async t => {
  const f = await readyFixture(t);
  await assert.rejects(assemble({ workspace: f.workspace, out: path.join(f.root, 'build') }), /verify your authorization/);
});
test('symlink image read and write cannot escape the workspace', async t => {
  const f = await fixture(t); await prepare({ script: f.source, out: f.workspace });
  const external = path.join(f.root, 'outside'); await fs.mkdir(external);
  await fs.rmdir(path.join(f.workspace, 'assets')); await fs.symlink(external, path.join(f.workspace, 'assets'), 'dir');
  const source = path.join(f.root, 'synthetic.png'); await fs.writeFile(source, png());
  await assert.rejects(recordImage({ workspace: f.workspace, id: 'P01', image: source, 'source-origin': 'test', 'tool-result-ref': 'test' }), /escapes workspace/);
  assert.deepEqual(await fs.readdir(external), []);
  await fs.writeFile(path.join(external, 'P01.png'), png());
  await assert.rejects(collectImages(await loadWorkspace(f.workspace)), /escapes workspace/);
});
test('changed prompt or receipt hash blocks use', async t => {
  const f = await readyFixture(t); const receiptPath = path.join(f.workspace, 'receipts/P01.json');
  const receipt = JSON.parse(await fs.readFile(receiptPath)); receipt.image_sha256 = 'wrong'; await fs.writeFile(receiptPath, jsonBytes(receipt));
  await assert.rejects(collectImages(await loadWorkspace(f.workspace)), /invalid image receipt/);
  await fs.appendFile(path.join(f.workspace, 'prompts/P01.md'), 'Changed');
  await assert.rejects(loadWorkspace(f.workspace), /prompt hash/);
});
test('PNG checks reject corrupted chunks and canvas ratio mismatch', async t => {
  const corrupt = png(); corrupt[20] ^= 1; assert.throws(() => inspectPng(corrupt), /checksum/);
  const f = await readyFixture(t, png(10, 10));
  await assert.rejects(collectImages(await loadWorkspace(f.workspace)), /aspect ratio mismatch/);
});
test('presentation adapter sends one original image per slide to a fake observer', () => {
  const calls = []; const observer = { slides: { add() { const slide = { background: {}, images: { add(value) { calls.push(value); } } }; return slide; } } };
  const Presentation = { create(options) { assert.deepEqual(options.slideSize, { width: 16, height: 9 }); return observer; } };
  assert.equal(createImagePresentation(Presentation, inputScript(), [{ bytes: png(), width: 16, height: 9 }]), observer);
  assert.equal(calls.length, 1); assert.deepEqual(calls[0].position, { left: 0, top: 0, width: 16, height: 9 });
  assert.equal(calls[0].dataUrl, `data:image/png;base64,${png().toString('base64')}`);
});
test('handoff copies files and preserves sources using an explicit synthetic fixture', async t => {
  const f = await readyFixture(t); const build = await syntheticBuild(f); const output = path.join(f.root, 'handoff');
  await createHandoff({ workspace: f.workspace, build, out: output });
  const manifest = JSON.parse(await fs.readFile(path.join(output, 'manifest.json')));
  assert.equal(manifest.kind, 'image_ppt_to_editable'); assert.deepEqual(manifest.independent_assets, []);
  assert.deepEqual(manifest.slides[0].source_refs, ['synthetic:test']); assert.equal(manifest.slides[0].generation.model_id, 'unknown');
  assert.deepEqual(await fs.readFile(path.join(output, 'assets/P01.png')), await fs.readFile(f.imagePath));
  await assert.rejects(createHandoff({ workspace: f.workspace, build, out: output }), /already exists/);
  await fs.appendFile(path.join(build, 'deck.pptx'), 'changed');
  await assert.rejects(createHandoff({ workspace: f.workspace, build, out: path.join(f.root, 'handoff2') }), /PPTX hash mismatch/);
});
test('CLI argument failures have a nonzero exit status', () => {
  const result = spawnSync(process.execPath, [path.join(here, '../scripts/prepare_prompts.mjs'), '--unexpected', 'x'], { encoding: 'utf8' });
  assert.notEqual(result.status, 0); assert.match(result.stderr, /ERROR: invalid argument/);
});
