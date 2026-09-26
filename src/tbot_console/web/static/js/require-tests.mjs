export default async function* requireTests(source) {
  let ran = 0;
  for await (const { type } of source) if (type === "test:pass" || type === "test:fail") ran++;
  if (ran > 0) return;
  process.exitCode = 1;
  yield "no tests ran\n";
}
