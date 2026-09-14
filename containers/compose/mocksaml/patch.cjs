// @ts-check
// Local test fixture only. Apply to the pinned dependency before SAML signing.
const fs = require('node:fs');

const packagePath = 'node_modules/@boxyhq/saml20';
/** @type {unknown} */
const packageMetadata = JSON.parse(fs.readFileSync(`${packagePath}/package.json`, 'utf8'));
if (typeof packageMetadata !== 'object' || packageMetadata === null ||
    !('version' in packageMetadata) || packageMetadata.version !== '1.12.2') {
  throw new Error('Unexpected @boxyhq/saml20 version; review the local fixture patch.');
}

/**
 * @param {string} path
 * @param {string} before
 * @param {string} after
 * @returns {void}
 */
function replaceOnce(path, before, after) {
  const source = fs.readFileSync(path, 'utf8');
  if (source.split(before).length !== 2) {
    throw new Error(`Expected exactly one patch match in ${path}; refusing to build.`);
  }
  fs.writeFileSync(path, source.replace(before, after));
}

const emailFormat = 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress';
const persistentFormat = 'urn:oasis:names:tc:SAML:2.0:nameid-format:persistent';
replaceOnce(`${packagePath}/dist/response.js`, emailFormat, persistentFormat);
replaceOnce(`${packagePath}/dist/response.js`, "'#text': claims.email,", "'#text': claims.raw.id,");
replaceOnce(
  `${packagePath}/dist/metadata.js`,
  `'md:NameIDFormat': {\n                    '#text': '${emailFormat}',\n                },\n                'md:SingleSignOnService':`,
  `'md:NameIDFormat': {\n                    '#text': '${persistentFormat}',\n                },\n                'md:SingleSignOnService':`,
);
// The mock accepts unsigned AuthnRequests; advertise its actual behavior.
replaceOnce('pages/api/saml/metadata.ts', 'wantAuthnRequestsSigned: true,', 'wantAuthnRequestsSigned: false,');
