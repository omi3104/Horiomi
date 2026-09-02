#!/usr/bin/env node
/**
 * Generate the PWA icons with zero dependencies (pure Node zlib PNG encoder).
 *   node docs/make-icons.mjs
 * Produces: icon-192.png, icon-512.png, apple-touch-icon.png (180)
 * Design: dark rounded square + amber "play" triangle.
 */
import { deflateSync } from "node:zlib";
import { writeFileSync } from "node:fs";

// Small CRC-32 (zlib's crc32 export only exists on Node >= 20.15 / 21).
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const BG = [14, 17, 22];       // #0E1116
const AMBER = [224, 168, 46];  // #E0A82E

function chunk(type, data) {
  const t = Buffer.from(type, "ascii");
  const body = Buffer.concat([t, data]);
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body) >>> 0, 0);
  return Buffer.concat([len, body, crc]);
}

function png(size) {
  const S = size;
  const r = S * 0.22;                 // corner radius
  const cx = S * 0.5, cy = S * 0.5;
  // play triangle vertices
  const ax = S * 0.40, ay = S * 0.30;
  const bx = S * 0.40, by = S * 0.70;
  const dx = S * 0.72, dy = S * 0.50;
  const sign = (px, py, x1, y1, x2, y2) => (px - x2) * (y1 - y2) - (x1 - x2) * (py - y2);

  const raw = Buffer.alloc((S * 3 + 1) * S);
  for (let y = 0; y < S; y++) {
    raw[y * (S * 3 + 1)] = 0; // filter: none
    for (let x = 0; x < S; x++) {
      let col = [0, 0, 0]; // outside rounded rect -> transparent-ish (kept opaque black-free via bg)
      // rounded-rect mask
      const inX = Math.min(x, S - 1 - x), inY = Math.min(y, S - 1 - y);
      let inside = true;
      if (inX < r && inY < r) {
        inside = Math.hypot(r - inX, r - inY) <= r;
      }
      if (!inside) {
        col = BG; // just paint bg outside the radius too (square-ish), keeps it simple/opaque
      } else {
        col = BG;
        const s1 = sign(x, y, ax, ay, bx, by);
        const s2 = sign(x, y, bx, by, dx, dy);
        const s3 = sign(x, y, dx, dy, ax, ay);
        const hasNeg = s1 < 0 || s2 < 0 || s3 < 0;
        const hasPos = s1 > 0 || s2 > 0 || s3 > 0;
        if (!(hasNeg && hasPos)) col = AMBER;
        // subtle ring
        const d = Math.hypot(x - cx, y - cy);
        if (d > S * 0.44 && d < S * 0.462) col = AMBER;
      }
      const o = y * (S * 3 + 1) + 1 + x * 3;
      raw[o] = col[0]; raw[o + 1] = col[1]; raw[o + 2] = col[2];
    }
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(S, 0);
  ihdr.writeUInt32BE(S, 4);
  ihdr[8] = 8;   // bit depth
  ihdr[9] = 2;   // color type: truecolor RGB
  ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

for (const [name, size] of [["icon-192.png", 192], ["icon-512.png", 512], ["apple-touch-icon.png", 180]]) {
  writeFileSync(new URL(`./${name}`, import.meta.url), png(size));
  console.log("wrote", name, size + "x" + size);
}
