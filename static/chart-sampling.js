"use strict";

// Preserve each visible time bucket's extrema and every null-gap boundary.
// Highly fragmented data may exceed the pixel budget rather than hide a gap.
window.MonitorChartSampling = {
  sample(points, pixelWidth) {
    const buckets = Math.max(1, Math.floor(pixelWidth) || 1);
    if (points.length <= buckets * 2 + 2) return points;
    const keep = new Set([0, points.length - 1]);
    const start = points[0][0], span = points[points.length - 1][0] - start;
    let bucket = -1, minimum = -1, maximum = -1;
    const flush = () => { if (minimum >= 0) { keep.add(minimum); keep.add(maximum); } };
    for (let i = 0; i < points.length; i += 1) {
      const current = points[i];
      if (i && (current[1] === null) !== (points[i - 1][1] === null)) {
        keep.add(i - 1); keep.add(i);
      }
      const next = Math.min(buckets - 1, Math.floor((span > 0 ? (current[0] - start) / span : i / points.length) * buckets));
      if (next !== bucket) { flush(); bucket = next; minimum = maximum = -1; }
      if (current[1] === null) continue;
      if (minimum < 0 || current[1] < points[minimum][1]) minimum = i;
      if (maximum < 0 || current[1] > points[maximum][1]) maximum = i;
    }
    flush();
    return [...keep].sort((a, b) => a - b).map(index => points[index]);
  },
};
