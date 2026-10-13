// Keep browser fixtures/screenshots out of the project root.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..', 'test_runs');

function artifactPath(name) {
  if (!/^\.tmp_[a-zA-Z0-9_-]+$/.test(name)) throw Error('Use a .tmp_ task name.');
  fs.mkdirSync(root, {recursive: true});
  return path.join(root, name);
}

module.exports = {artifactPath};
