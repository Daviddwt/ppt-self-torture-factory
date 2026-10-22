import fs from 'node:fs/promises';
import path from 'node:path';
import { buildPrompt, cliArgs, exclusiveWrite, failureMarker, isMain, jsonBytes, newDirectory, pathsFor, readRegular, runCli, sha256, validateScript } from './common.mjs';

export async function prepare({ script: input, out }) {
  const bytes = await readRegular(path.resolve(input));
  const script = validateScript(JSON.parse(bytes));
  let output;
  try {
    output = await newDirectory(out);
    for (const dir of ['prompts', 'assets', 'receipts']) await fs.mkdir(path.join(output, dir));
    await exclusiveWrite(path.join(output, 'script.json'), bytes);
    const slides = [];
    for (const slide of script.slides) {
      const record = { id: slide.id, ...pathsFor(slide.id) };
      const prompt = buildPrompt(script, slide);
      await exclusiveWrite(path.join(output, record.prompt_path), prompt);
      slides.push({ ...record, prompt_sha256: sha256(prompt) });
    }
    const manifest = { schema_version: '1.0', kind: 'prepared_image_deck', status: 'awaiting_images', title: script.title, canvas: script.canvas, script_sha256: sha256(bytes), image_model_requested: 'codex_builtin_imagegen', model_id: 'unknown', slides };
    await exclusiveWrite(path.join(output, 'manifest.json'), jsonBytes(manifest));
    return path.join(output, 'manifest.json');
  } catch (error) { await failureMarker(output, error); throw error; }
}
if (isMain(import.meta.url)) await runCli(() => prepare(cliArgs(process.argv.slice(2), ['script', 'out'], ['script', 'out'])));
