/**
 * Ward geometry for scripts: a CommonJS port of src/lib/wards.ts.
 *
 * resolveWard() uses the same polygon normalisation, bounding-box pre-filter,
 * ray-casting test and first-hit / ambiguity rules as the TypeScript module the
 * complaints API uses, so a point the generator accepts is one the API would
 * accept for the same ward.
 */
const fs = require("fs");
const path = require("path");

const GEOJSON_PATH = path.join(__dirname, "..", "..", "data", "boundaries", "gcc-wards.geojson");

let cached = null;

function ringBbox(rings) {
  let minLng = 180, minLat = 90, maxLng = -180, maxLat = -90;
  for (const ring of rings) {
    for (const [lng, lat] of ring) {
      if (lng < minLng) minLng = lng;
      if (lng > maxLng) maxLng = lng;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
    }
  }
  return [minLng, minLat, maxLng, maxLat];
}

/** Every polygon, in file order, as { wardNo, zoneNumber, zoneName, bbox, rings }. */
function loadWardFeatures() {
  if (cached) return cached;
  const gj = JSON.parse(fs.readFileSync(GEOJSON_PATH, "utf8"));
  const features = [];
  for (const f of gj.features || []) {
    const g = f.geometry;
    if (!g) continue;
    const polygons = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
    for (const rings of polygons) {
      features.push({
        wardNo: f.properties.ward_no,
        zoneNumber: f.properties.zone_number ?? null,
        zoneName: f.properties.zone_name ?? null,
        bbox: ringBbox(rings),
        rings
      });
    }
  }
  cached = features;
  return features;
}

function pointInRing(lng, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const intersects = yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

function pointInPolygon(lng, lat, rings) {
  if (rings.length === 0) return false;
  if (!pointInRing(lng, lat, rings[0])) return false;
  for (let i = 1; i < rings.length; i++) {
    if (pointInRing(lng, lat, rings[i])) return false;
  }
  return true;
}

/** Same contract as resolveWard() in src/lib/wards.ts (minus provenance). */
function resolveWard(lat, lng) {
  const hits = [];
  for (const f of loadWardFeatures()) {
    const [minLng, minLat, maxLng, maxLat] = f.bbox;
    if (lng < minLng || lng > maxLng || lat < minLat || lat > maxLat) continue;
    if (pointInPolygon(lng, lat, f.rings)) hits.push(f);
  }
  if (hits.length === 0) return { status: "outside_boundary" };
  const wardNumbers = Array.from(new Set(hits.map((h) => h.wardNo)));
  return {
    status: "resolved",
    wardNo: hits[0].wardNo,
    zoneNumber: hits[0].zoneNumber,
    zoneName: hits[0].zoneName,
    ambiguous: wardNumbers.length > 1,
    candidates: wardNumbers.length > 1 ? wardNumbers : undefined
  };
}

/** True when the point lies inside one of the given ward's polygons. */
function pointInWard(lat, lng, wardNo) {
  return loadWardFeatures().some((f) => {
    if (f.wardNo !== wardNo) return false;
    const [minLng, minLat, maxLng, maxLat] = f.bbox;
    if (lng < minLng || lng > maxLng || lat < minLat || lat > maxLat) return false;
    return pointInPolygon(lng, lat, f.rings);
  });
}

/** Great-circle distance in metres. */
function haversineM(lat1, lng1, lat2, lng2) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

module.exports = { loadWardFeatures, resolveWard, pointInWard, haversineM };
