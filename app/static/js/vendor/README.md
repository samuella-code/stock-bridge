# Barcode decoder

`zxing-browser-0.2.1.min.js` is the unmodified UMD bundle from the official npm package `@zxing/browser@0.2.1` (`package/umd/zxing-browser.min.js`). It is lazy-loaded only for camera fallback or barcode-photo decoding and is served from this application's origin.

Source: https://github.com/zxing-js/browser

Package SHA-512 integrity: `sha512-92pVfVDUXbc15xu9vIEmhNyqIEASMEkDF92CwT6T+7sg2EHXJRktH9CQKoQSXe51UtbNsj+Fm09H/JBWHMs/Sw==`

Bundle SHA-256: `066bc34edfcdd4a33f0964aeec967752a0dea1ccaf36e58e319ac9fcb5070f6a`

The bundle contains the ZXing browser layer (MIT) and ZXing library decoding implementation (Apache-2.0). License texts are preserved beside the bundle as `ZXING-BROWSER-LICENSE.txt` and `ZXING-LIBRARY-LICENSE.txt`. Source maps are not deployed. Upgrades should replace the versioned asset and license texts, update the URL and checksum, and rerun `tests/mobile_ui.cjs` with the actual replacement bundle.

No npm install or JavaScript build step is needed in Vercel. No camera frames or photos are transmitted by this integration.
