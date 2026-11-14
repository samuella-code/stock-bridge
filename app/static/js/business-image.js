/* Phone-image optimization avoids Vercel's 4.5 MB request-body ceiling. */
(() => {
  const uploadLimit = 4 * 1024 * 1024;
  const compactTarget = 500 * 1024;

  async function encodeUpload(canvas, sourceType) {
    const formats = sourceType === 'image/jpeg'
      ? [['image/webp', 'webp'], ['image/jpeg', 'jpg']]
      : [['image/webp', 'webp'], ['image/png', 'png']];
    const encode = (type, quality) => new Promise(resolve => canvas.toBlob(resolve, type, quality));

    for (const [type, extension] of formats) {
      const preferred = await encode(type, 0.85);
      if (preferred?.type !== type || preferred.size === 0) continue;
      if (preferred.size <= compactTarget || (type === 'image/png' && preferred.size <= uploadLimit)) {
        return { blob: preferred, filename: `business-logo.${extension}` };
      }
      if (type === 'image/png') continue;
      const compact = await encode(type, 0.75);
      const blob = compact?.type === type && compact.size > 0 && compact.size < preferred.size ? compact : preferred;
      if (blob.size <= uploadLimit) return { blob, filename: `business-logo.${extension}` };
    }
    throw new Error('This image could not be prepared for upload. Try a smaller JPEG or PNG.');
  }

  document.querySelectorAll('[data-business-image]').forEach(form => {
    const input = form.querySelector('input[type=file]');
    const status = form.querySelector('[data-image-status]');
    const preview = form.querySelector('[data-image-preview]');
    const button = form.querySelector('button[type=submit]');
    let busy = false;
    let previewURL = null;
    input.addEventListener('change', () => {
      if (previewURL) URL.revokeObjectURL(previewURL);
      preview.hidden = true;
      status.textContent = '';
      const file = input.files[0];
      if (file && ['image/jpeg', 'image/png', 'image/webp'].includes(file.type) && file.size <= 5 * 1024 * 1024) {
        previewURL = URL.createObjectURL(file); preview.src = previewURL; preview.hidden = false;
      }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      const file = input.files[0];
      if (!file || file.size > 5 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
        status.textContent = 'Choose a JPG, PNG or WebP no larger than 5 MB (5 MiB).'; return;
      }
      busy = true; button.disabled = true;
      status.textContent = 'Optimizing your image…';
      let bitmap;
      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        const ascii = (start, length) => String.fromCharCode(...bytes.slice(start, start + length));
        const isJPEG = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
        const isPNG = bytes[0] === 137 && ascii(1, 3) === 'PNG' && bytes[4] === 13 && bytes[5] === 10 && bytes[6] === 26 && bytes[7] === 10;
        const isWebP = ascii(0, 4) === 'RIFF' && ascii(8, 4) === 'WEBP';
        if (!(file.type === 'image/jpeg' ? isJPEG : file.type === 'image/png' ? isPNG : isWebP)) throw new Error('Choose a valid JPG, PNG or WebP image.');
        if (isWebP && ascii(12, 4) === 'VP8X' && (bytes[20] & 2)) throw new Error('Choose a non-animated image.');
        if (isPNG) {
          const view = new DataView(bytes.buffer);
          for (let offset = 8; offset + 12 <= bytes.length;) {
            const length = view.getUint32(offset);
            if (ascii(offset + 4, 4) === 'acTL') throw new Error('Choose a non-animated image.');
            if (length > bytes.length - offset - 12) break;
            offset += 12 + length;
          }
        }
        bitmap = await createImageBitmap(file, {imageOrientation: 'from-image'});
        if (bitmap.width * bitmap.height > 12000000) throw new Error('Image exceeds 12 megapixels.');
        const ratio = Math.min(1, 1024 / bitmap.width, 1024 / bitmap.height);
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(bitmap.width * ratio));
        canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
        canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        const { blob, filename } = await encodeUpload(canvas, file.type);
        const data = new FormData(form);
        data.set('logo', blob, filename);
        status.textContent = 'Uploading optimized image…';
        const response = await fetch(form.action, {method: 'POST', body: data, credentials: 'same-origin'});
        if (!response.ok) throw new Error('Upload failed. Your current image is preserved.');
        window.location.assign(response.url);
      } catch (error) {
        status.textContent = error.message || 'Could not read this image.';
        button.disabled = false; busy = false;
      } finally { if (bitmap) bitmap.close(); }
    });
  });
})();
