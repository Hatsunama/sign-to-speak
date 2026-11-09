import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import test from 'node:test';
const native = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = p => readFileSync(path.join(native, p), 'utf8');

test('every app variant has an owned, verified Holistic asset producer', () => {
  const build = read('android/app/build.gradle');
  const source = read('android/app/offline-landmark-runtime.gradle');
  assert.match(build, /apply from: "offline-landmark-runtime\.gradle"/);
  assert.match(source, /onVariants\(selector\(\)\.all\(\)\)/);
  assert.match(source, /variant\.sources\.assets\.addGeneratedSourceDirectory\(stageHolisticAssets\)/);
  assert.match(source, /@OutputDirectory abstract DirectoryProperty getOutputDirectory/);
  assert.match(source, /prepare-holistic-assets\.py/);
  assert.match(source, /outputs\.upToDateWhen \{ false \}/);
  assert.match(source, /dependsOn\(verify\)/);
  assert.doesNotMatch(source, /abiFilters|pickFirst|tools:node/);
  assert.match(source, /keepDebugSymbols\.add\("\*\*\/libmediapipe_tasks_jni\.so"\)/);
});

test('offline source substitution covers app and instrumentation without telemetry exclusions', () => {
  const source = read('android/app/offline-landmark-runtime.gradle');
  assert.match(source, /configurations\.configureEach/);
  assert.match(source, /substitute\(module\("com\.google\.mediapipe:tasks-core"\)\)/);
  assert.match(source, /implementation\("com\.google\.mediapipe:tasks-vision:1\.1\.1"\)/);
  assert.match(source, /com\.google\.android\.datatransport/);
  assert.match(source, /selected_coordinates: coordinates/);
  assert.doesNotMatch(source, /exclude\s*\(?group|transitive\s*=\s*false/);
  const qa = read('android/app/phone-qa.gradle');
  assert.doesNotMatch(qa, /androidTestImplementation\("com\.google\.mediapipe:tasks-vision/);
  assert.match(qa, /androidTestImplementation\("com\.google\.ai\.edge\.litert:litert:1\.4\.2"\)/);
});

test('runtime audit separates complete graph evidence from external artifact file resolution', () => {
  const source = read('android/app/offline-landmark-runtime.gradle');
  assert.doesNotMatch(source, /inputs\.files\(runtime\)|resolvedConfiguration|resolvedArtifacts/);
  assert.match(source, /incoming\.resolutionResult/);
  assert.match(source, /resolution\.allComponents/);
  assert.match(source, /resolution\.allDependencies/);
  assert.match(source, /instanceof ProjectComponentIdentifier/);
  assert.match(source, /instanceof ModuleComponentIdentifier/);
  assert.match(source, /componentFilter \{ id -> id instanceof ModuleComponentIdentifier \}/);
  assert.match(source, /attribute\(ArtifactTypeDefinition\.ARTIFACT_TYPE_ATTRIBUTE, "aar"\)/);
  assert.match(source, /inputs\.files\(externalArtifacts\.files, sdkArtifacts\.files\)/);
  assert.match(source, /components: graphComponents, dependencies: graphDependencies/);
  assert.match(source, /artifact_count:/);
  assert.match(source, /endsWith\("\.aar"\).*endsWith\("\.jar"\)/);
  assert.doesNotMatch(source, /lenient\s*=?\s*true|withVariantReselection|releaseRuntimeElements/);
});

test('model and all SDK grant texts have explicit pinned app destinations', () => {
  const receipt = JSON.parse(read('vendor/mediapipe-offline/runtime-admission.json'));
  assert.equal(receipt.model.asset, 'holistic/holistic_landmarker.task');
  assert.equal(receipt.model.size, 13683609);
  assert.equal(receipt.model.sha256, 'e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8');
  assert.equal(receipt.model.members.length, 7);
  assert.equal(receipt.runtime.usage_logging, false);
  assert.equal(receipt.runtime.jni.abi, 'arm64-v8a');
  for (const row of receipt.notices) {
    const bytes = readFileSync(path.join(native, 'vendor/mediapipe-offline', row.source));
    assert.equal(bytes.length, row.size);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), row.sha256);
    assert.ok(row.asset.startsWith('holistic-licenses/'));
  }
  assert.equal(receipt.notices.length, 6);
});

test('newly resolved checker compatibility annotations carry the exact MIT license route', () => {
  const receipt = JSON.parse(read('vendor/mediapipe-offline/runtime-admission.json'));
  const asset = 'holistic-licenses/checker-framework-2.5.3-LICENSE.txt';
  const notice = receipt.notices.find(row => row.asset === asset);
  assert.ok(notice, 'The new annotation dependency must have a packaged grant');
  assert.equal(notice.size, 22320);
  assert.equal(notice.sha256, '37ac781fd633d592519cc231473b9a25ebd84c3bbe829abb8f588d3355edc63c');
  const grant = read(`vendor/mediapipe-offline/${notice.source}`);
  assert.match(grant, /The annotations are licensed under the MIT License/);
  assert.match(grant, /MIT License:\s+Permission is hereby granted/);
  const routes = receipt.runtime_license_routes;
  assert.equal(routes.length, 12); // Eleven additions plus Guava's empty conflict-avoidance replacement.
  assert.equal(new Set(routes.map(row => row.coordinate)).size, routes.length);
  const checker = routes.find(row => row.coordinate === 'org.checkerframework:checker-compat-qual:2.5.3');
  assert.equal(checker.license, 'MIT');
  assert.equal(checker.asset, asset);
  assert.equal(checker.artifact_sha256, 'd76b9afea61c7c082908023f0cbc1427fab9abd2df915c8b8a3e7a509bccbc6d');
  assert.equal(routes.filter(row => row.scope === 'detector-only').length, 11);
  const runtime = read('android/app/offline-landmark-runtime.gradle');
  assert.match(runtime, /resolution\.rootComponent\.get\(\)\.id/);
  assert.match(runtime, /introduced != licenseRoutes\.findAll \{ it\.scope == "detector-only" \}/);
  assert.match(runtime, /digestFile\(matched\[0\]\.file\) != route\.artifact_sha256/);
  assert.match(runtime, /!admissionReceipt\.notices\.any \{ it\.asset == route\.asset \}/);
  assert.match(runtime, /verified_license_routes: licenseRoutes/);
});
