import path from 'node:path';
import { cliArgs, exclusiveWriteInside, inspectPng, isMain, jsonBytes, loadWorkspace, mustNotExist, readRegular, requireText, runCli, sha256 } from './common.mjs';

export async function recordImage(options) {
  const workspace = await loadWorkspace(options.workspace);
  const record = workspace.manifest.slides.find(slide => slide.id === options.id);
  if (!record) throw new Error('slide id not found');
  for (const key of ['source-origin', 'tool-result-ref']) requireText(options[key], `--${key}`);
  const model = options.model === undefined ? 'unknown' : requireText(options.model, '--model');
  const bytes = await readRegular(path.resolve(options.image));
  const dimensions = inspectPng(bytes);
  const target = path.join(workspace.root, record.image_path);
  const receiptPath = path.join(workspace.root, record.receipt_path);
  await mustNotExist(target); await mustNotExist(receiptPath);
  const receipt = {
    schema_version: '1.0', id: record.id, tool: 'codex_builtin_imagegen',
    model_id: model, source_origin: options['source-origin'],
    tool_result_reference: options['tool-result-ref'], prompt_sha256: record.prompt_sha256,
    image_sha256: sha256(bytes), ...dimensions, recorded_at: new Date().toISOString(),
  };
  // Save the original bytes; neither resize nor recompress the source image.
  await exclusiveWriteInside(workspace.root, record.image_path, bytes);
  await exclusiveWriteInside(workspace.root, record.receipt_path, jsonBytes(receipt));
  return receiptPath;
}
if (isMain(import.meta.url)) await runCli(() => recordImage(cliArgs(process.argv.slice(2), ['workspace', 'id', 'image', 'source-origin', 'tool-result-ref', 'model'], ['workspace', 'id', 'image', 'source-origin', 'tool-result-ref'])));
