/* Shared, DOM-free geometry for the signal-map selection. */
(function (root) {
  const clamp = (value, lower, upper) => Math.max(lower, Math.min(upper, value));

  function minimumSpan(axis) {
    let span = 0;
    for (let i = 1; i < axis.length; i += 1) {
      span = Math.max(span, axis[i] - axis[i - 1]);
    }
    return span;
  }

  function drawInterval(start, end, lower, upper, minimum) {
    const size = Math.max(Math.abs(end - start), minimum);
    const left = clamp(end < start ? start - size : start, lower, upper - size);
    return [left, left + size];
  }

  function adjust(original, start, point, mode, axes) {
    const result = { ...original };
    for (const [axis, lowHandle, highHandle] of [["x", "w", "e"], ["y", "s", "n"]]) {
      const values = axes[axis];
      const lower = values[0];
      const upper = values[values.length - 1];
      const low = axis + "_min";
      const high = axis + "_max";
      const minimum = minimumSpan(values);
      const delta = point[axis] - start[axis];
      if (mode === "draw") {
        [result[low], result[high]] = drawInterval(
          clamp(start[axis], lower, upper), clamp(point[axis], lower, upper), lower, upper, minimum
        );
      } else if (mode === "move") {
        const shift = clamp(delta, lower - original[low], upper - original[high]);
        result[low] = original[low] + shift;
        result[high] = original[high] + shift;
      } else if (mode.includes(lowHandle)) {
        result[low] = clamp(original[low] + delta, lower, original[high] - minimum);
      } else if (mode.includes(highHandle)) {
        result[high] = clamp(original[high] + delta, original[low] + minimum, upper);
      }
    }
    return result;
  }

  function handles(rect) {
    const { left, right, top, bottom } = rect;
    const cx = (left + right) / 2;
    const cy = (top + bottom) / 2;
    return [
      ["nw", left, top], ["ne", right, top], ["sw", left, bottom], ["se", right, bottom],
      ["n", cx, top], ["s", cx, bottom], ["w", left, cy], ["e", right, cy],
    ].map(([mode, x, y]) => ({ mode, x, y }));
  }

  root.EelsRoi = { adjust, handles };
  if (typeof module !== "undefined" && module.exports) module.exports = root.EelsRoi;
})(globalThis);
