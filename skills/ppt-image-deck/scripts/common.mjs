import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
export const jsonBytes = value => `${JSON.stringify(value, null, 2)}\n`;
export function requireText(value, name) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${name} must be a nonempty string`);
  return value;
}
export function validateScript(value) {
  if (!value || value.schema_version !== '1.0') throw new Error('script.schema_version must be 1.0');
  requireText(value.title, 'script.title');
  for (const key of ['width', 'height']) {
    const n = value.canvas?.[key];
    if (!Number.isInteger(n) || n < 1 || n > 16384) throw new Error(`canvas.${key} must be an integer from 1 to 16384`);
  }
  if (!Array.isArray(value.slides) || !value.slides.length || value.slides.length > 500) throw new Error('script.slides must contain 1 to 500 slides');
  const seen = new Set();
  for (const slide of value.slides) {
    if (!slide || typeof slide.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(slide.id)) throw new Error('slide id must use 1 to 64 ASCII letters, digits, underscores or hyphens');
    if (seen.has(slide.id.toLowerCase())) throw new Error(`duplicate slide id: ${slide.id}`);
    seen.add(slide.id.toLowerCase());
    for (const key of ['title', 'key_message', 'image_prompt']) requireText(slide[key], `${slide.id}.${key}`);
    if (typeof slide.speaker_notes !== 'string') throw new Error(`${slide.id}.speaker_notes must be a string`);
    for (const key of ['on_slide_text', 'source_refs']) {
      if (!Array.isArray(slide[key]) || slide[key].some(item => typeof item !== 'string' || !item.trim())) throw new Error(`${slide.id}.${key} must be an array of nonempty strings (the array may be empty)`);
    }
  }
  return value;
}
export function portableRelative(value) {
  requireText(value, 'relative path');
  if (value.includes('\\') || value.includes(':') || value.includes('\0') || path.posix.isAbsolute(value) || value.split('/').some(part => !part || part === '.' || part === '..')) throw new Error(`unsafe relative path: ${value}`);
  return value;
}
export function within(root, target) {
  const rel = path.relative(root, target);
  return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel));
}
export async function readRegular(file) {
  const stat = await fs.lstat(file);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`expected a regular file: ${file}`);
  return fs.readFile(file);
}
export async function readInside(root, relative) {
  portableRelative(relative);
  const absolute = path.resolve(root, relative);
  const real = await fs.realpath(absolute);
  if (!within(root, real)) throw new Error(`path escapes workspace: ${relative}`);
  return readRegular(absolute);
}
export async function mustNotExist(target) {
  try { await fs.lstat(target); } catch (error) { if (error.code === 'ENOENT') return; throw error; }
  throw new Error(`output already exists: ${target}`);
}
export async function outputPath(target, excludedRoots = []) {
  const output = path.resolve(target);
  const parent = await fs.realpath(path.dirname(output));
  const actual = path.join(parent, path.basename(output));
  for (const root of excludedRoots) {
    if (within(root, actual) || within(actual, root)) throw new Error('output must be separate from input directories');
  }
  await mustNotExist(output);
  return actual;
}
export async function newDirectory(target, excludedRoots = []) {
  const actual = await outputPath(target, excludedRoots);
  await fs.mkdir(actual); // EEXIST also protects against concurrent writers.
  return actual;
}
export async function exclusiveWrite(file, bytes) { await fs.writeFile(file, bytes, { flag: 'wx' }); }
export async function exclusiveWriteInside(root, relative, bytes) {
  portableRelative(relative);
  const target = path.resolve(root, relative);
  const parent = await fs.realpath(path.dirname(target));
  if (!within(root, parent)) throw new Error(`write path escapes workspace: ${relative}`);
  await exclusiveWrite(path.join(parent, path.basename(target)), bytes);
}
export async function failureMarker(output, error) {
  if (output) await exclusiveWrite(path.join(output, 'FAILED.json'), jsonBytes({ status: 'failed', error: error.message, partial_outputs: true })).catch(() => {});
}
export function pathsFor(id) { return { prompt_path: `prompts/${id}.md`, image_path: `assets/${id}.png`, receipt_path: `receipts/${id}.json` }; }
export function buildPrompt(script, slide) {
  return [
    `# ${slide.id} — ${slide.title}`,
    `Create one complete raster presentation slide. Canvas: ${script.canvas.width} × ${script.canvas.height}.`,
    'Use the requested aspect ratio. Produce an opaque full-page image.',
    'Render the following approved on-slide text exactly. Add no factual claims or extra labels.',
    '', '## Approved on-slide text', slide.title, ...slide.on_slide_text,
    '', '## Key message', slide.key_message,
    '', '## Visual instructions', slide.image_prompt,
    '', '## Speaker guidance — keep this out of the slide body', slide.speaker_notes,
    '', '## Source references — retain as evidence, not as slide text', ...slide.source_refs.map(ref => `- ${ref}`), '',
  ].join('\n');
}
export async function loadWorkspace(workspace) {
  const root = await fs.realpath(path.resolve(workspace));
  const manifest = JSON.parse(await readInside(root, 'manifest.json'));
  if (manifest.schema_version !== '1.0' || manifest.kind !== 'prepared_image_deck') throw new Error('unsupported prepared manifest');
  const scriptBytes = await readInside(root, 'script.json');
  const script = validateScript(JSON.parse(scriptBytes));
  if (sha256(scriptBytes) !== manifest.script_sha256) throw new Error('script hash mismatch');
  if (!Array.isArray(manifest.slides) || manifest.slides.length !== script.slides.length) throw new Error('manifest slide count mismatch');
  for (let i = 0; i < script.slides.length; i++) {
    const slide = script.slides[i];
    const record = manifest.slides[i];
    if (record.id !== slide.id) throw new Error('manifest slide order mismatch');
    for (const [key, value] of Object.entries(pathsFor(slide.id))) {
      portableRelative(record[key]);
      if (record[key] !== value) throw new Error(`unexpected ${key} for ${slide.id}`);
    }
    const prompt = await readInside(root, record.prompt_path);
    if (sha256(prompt) !== record.prompt_sha256 || prompt.toString('utf8') !== buildPrompt(script, slide)) throw new Error(`prompt hash/content mismatch: ${slide.id}`);
  }
  return { root, manifest, script, scriptBytes };
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
export function inspectPng(bytes) {
  if (bytes.length < 45 || bytes.length > 64 * 1024 * 1024 || !bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) throw new Error('invalid PNG signature or file size');
  let offset = 8, width, height, imageData = false, ended = false;
  while (offset < bytes.length) {
    if (offset + 12 > bytes.length) throw new Error('truncated PNG chunk');
    const length = bytes.readUInt32BE(offset), end = offset + 12 + length;
    if (end > bytes.length) throw new Error('truncated PNG data');
    const type = bytes.toString('ascii', offset + 4, offset + 8);
    if (crc32(bytes.subarray(offset + 4, end - 4)) !== bytes.readUInt32BE(end - 4)) throw new Error('PNG checksum mismatch');
    if (offset === 8) {
      if (type !== 'IHDR' || length !== 13) throw new Error('PNG missing IHDR');
      width = bytes.readUInt32BE(offset + 8); height = bytes.readUInt32BE(offset + 12);
      if (!width || !height || width > 16384 || height > 16384) throw new Error('PNG dimensions out of range');
    } else if (type === 'IHDR') throw new Error('duplicate PNG IHDR');
    if (type === 'IDAT') imageData = true;
    if (type === 'IEND') { if (length || end !== bytes.length) throw new Error('invalid PNG end'); ended = true; }
    offset = end;
  }
  if (!imageData || !ended) throw new Error('incomplete PNG');
  return { width, height };
}
export async function collectImages(workspace) {
  const result = [];
  for (let index = 0; index < workspace.manifest.slides.length; index++) {
    const record = workspace.manifest.slides[index];
    const bytes = await readInside(workspace.root, record.image_path);
    const dimensions = inspectPng(bytes);
    const ratioError = Math.abs(dimensions.width / dimensions.height / (workspace.script.canvas.width / workspace.script.canvas.height) - 1);
    if (ratioError > 0.005) throw new Error(`image aspect ratio mismatch: ${record.id}; regenerate without cropping the original`);
    const receiptBytes = await readInside(workspace.root, record.receipt_path);
    const receipt = JSON.parse(receiptBytes);
    if (receipt.schema_version !== '1.0' || receipt.id !== record.id || receipt.tool !== 'codex_builtin_imagegen' || receipt.image_sha256 !== sha256(bytes) || receipt.prompt_sha256 !== record.prompt_sha256) throw new Error(`invalid image receipt: ${record.id}`);
    for (const key of ['source_origin', 'tool_result_reference', 'model_id']) requireText(receipt[key], `${record.id}.${key}`);
    result.push({ ...record, ...dimensions, image_sha256: sha256(bytes), receipt_sha256: sha256(receiptBytes), generation: receipt, bytes });
  }
  return result;
}
export function cliArgs(argv, allowed, required = []) {
  const values = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i]?.replace(/^--/, '');
    if (!argv[i]?.startsWith('--') || !allowed.includes(key) || !argv[i + 1] || argv[i + 1].startsWith('--') || key in values) throw new Error(`invalid argument: ${argv[i]}`);
    values[key] = argv[i + 1];
  }
  for (const key of required) requireText(values[key], `--${key}`);
  return values;
}
export function isMain(url) { return Boolean(process.argv[1]) && url === pathToFileURL(path.resolve(process.argv[1])).href; }
export async function runCli(action) {
  try { console.log(await action()); } catch (error) { console.error(`ERROR: ${error.message}`); process.exitCode = 1; }
}
