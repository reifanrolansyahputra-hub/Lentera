// Item Selector LENTERA (port JS dari belajar/engine.py -> pilih_item). Berjalan offline di perangkat murid.
const G = 0.2, S = 0.1;
const peluangBenar = (p, lvSiswa, lvItem) => Math.min(0.97, Math.max(0.03, p * (1 - S) + (1 - p) * G - 0.15 * (lvItem - lvSiswa)));
// kandidat = [{id, level}], riwayat = id soal yang sudah muncul. Zona tantangan: peluang benar terdekat 70%.
export function pilihItem(kandidat, pL, level, riwayat = []) {
  const pool = kandidat.filter((k) => !riwayat.slice(-5).includes(k.id));
  return (pool.length ? pool : kandidat).reduce((a, b) =>
    Math.abs(peluangBenar(pL, level, b.level) - 0.7) < Math.abs(peluangBenar(pL, level, a.level) - 0.7) ? b : a);
}
