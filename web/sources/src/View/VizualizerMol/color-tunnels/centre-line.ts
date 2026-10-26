// The points of a channel's centreline, kept as plain coordinates so looking them up allocates nothing
export type CentreLine = { x: Float64Array, y: Float64Array, z: Float64Array };

export function centreLineOf(points: { X: number, Y: number, Z: number }[]): CentreLine {
    return {
        x: Float64Array.from(points, p => p.X),
        y: Float64Array.from(points, p => p.Y),
        z: Float64Array.from(points, p => p.Z),
    };
}

// The index of the centreline point closest to (x, y, z), the first one of equally close points.
// The interpolated centreline has about a point per ångström, so scanning it is fast enough.
export function closestPoint(line: CentreLine, x: number, y: number, z: number): number {
    const { x: xs, y: ys, z: zs } = line;
    let closest = 0;
    let minSq = Infinity;
    for (let i = 0; i < xs.length; i++) {
        const dx = xs[i] - x, dy = ys[i] - y, dz = zs[i] - z;
        const dSq = dx * dx + dy * dy + dz * dz;
        if (dSq < minSq) {
            minSq = dSq;
            closest = i;
        }
    }
    return closest;
}
