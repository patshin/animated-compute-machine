const input = process.argv[2] ?? "";
const match = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(input);

if (!match) {
  process.exit(2);
}

const [, majorText, minorText] = match;
const major = Number(majorText);
const minor = Number(minorText);
const supported = (major === 24 && minor >= 15) || major === 26;

if (!supported) {
  process.exit(1);
}

process.stdout.write(`node-version=${input.replace(/^v/, "")}\n`);
