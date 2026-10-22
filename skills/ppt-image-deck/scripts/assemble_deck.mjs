import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { cliArgs, collectImages, exclusiveWrite, failureMarker, isMain, jsonBytes, loadWorkspace, newDirectory, outputPath, readRegular, runCli, sha256 } from './common.mjs';

async function loadRuntime(options) {
  if (options['runtime-authorized'] !== 'yes') throw new Error('verify your authorization to use @oai/artifact-tool, then pass --runtime-authorized yes');
  let modulePath;
  if (options['artifact-module']) {
    modulePath = path.resolve(options['artifact-module']);
  } else {
    const resolver = createRequire(path.join(process.cwd(), '__image_deck_runtime_resolver__.cjs'));
    try { modulePath = resolver.resolve('@oai/artifact-tool'); }
    catch { throw new Error('@oai/artifact-tool is unavailable; provide your existing authorized module with --artifact-module. No package will be installed.'); }
  }
  const stat = await fs.lstat(modulePath);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('artifact module must be an existing regular file');
  const runtime = await import(pathToFileURL(modulePath).href);
  if (typeof runtime.Presentation?.create !== 'function' || typeof runtime.PresentationFile?.exportPptx !== 'function') throw new Error('artifact runtime does not expose the expected Presentation API');
  return runtime;
}
export function createImagePresentation(Presentation, script, images) {
  const { width, height } = script.canvas;
  const deck = Presentation.create({ slideSize: { width, height } });
  for (const image of images) {
    const slide = deck.slides.add();
    slide.background.fill = '#FFFFFF';
    const scale = Math.min(width / image.width, height / image.height);
    const shownWidth = image.width * scale, shownHeight = image.height * scale;
    slide.images.add({
      dataUrl: `data:image/png;base64,${image.bytes.toString('base64')}`,
      position: { left: (width - shownWidth) / 2, top: (height - shownHeight) / 2, width: shownWidth, height: shownHeight },
    });
  }
  return deck;
}
export async function assemble(options) {
  const workspace = await loadWorkspace(options.workspace);
  const images = await collectImages(workspace);
  await outputPath(options.out, [workspace.root]);
  // Complete input checks before importing or calling the external runtime.
  const runtime = await loadRuntime(options);
  let output;
  try {
    output = await newDirectory(options.out, [workspace.root]);
    const deck = createImagePresentation(runtime.Presentation, workspace.script, images);
    const file = await runtime.PresentationFile.exportPptx(deck);
    if (typeof file?.save !== 'function') throw new Error('artifact runtime export did not provide save()');
    const deckPath = path.join(output, 'deck.pptx');
    await file.save(deckPath);
    const bytes = await readRegular(deckPath);
    if (bytes.length < 22 || bytes.readUInt32LE(0) !== 0x04034b50) throw new Error('export is not a ZIP-based PPTX file');
    const manifest = {
      schema_version: '1.0', kind: 'assembled_image_deck', status: 'review_required',
      title: workspace.script.title, canvas: workspace.script.canvas,
      script_sha256: workspace.manifest.script_sha256, deck_path: 'deck.pptx', deck_sha256: sha256(bytes),
      runtime: { name: '@oai/artifact-tool', version: 'unknown', authorization_acknowledged: true },
      slides: images.map(({ bytes: _bytes, ...image }) => image),
      qa: { png_structure: 'passed', png_visual_review: 'pending', pptx_ooxml: 'pending', powerpoint_gui: 'pending' },
    };
    await exclusiveWrite(path.join(output, 'build-manifest.json'), jsonBytes(manifest));
    return path.join(output, 'build-manifest.json');
  } catch (error) { await failureMarker(output, error); throw error; }
}
if (isMain(import.meta.url)) await runCli(() => assemble(cliArgs(process.argv.slice(2), ['workspace', 'out', 'artifact-module', 'runtime-authorized'], ['workspace', 'out', 'runtime-authorized'])));
