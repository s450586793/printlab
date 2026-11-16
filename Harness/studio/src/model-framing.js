export function modelFrameDistance(radius, verticalFovDegrees, aspect, margin = 1.12) {
  if (![radius, verticalFovDegrees, aspect, margin].every(Number.isFinite)
      || radius <= 0 || verticalFovDegrees <= 0 || verticalFovDegrees >= 180 || aspect <= 0 || margin < 1) {
    throw new RangeError('Model framing requires positive bounds and a valid perspective.');
  }
  const vertical = verticalFovDegrees * Math.PI / 180;
  const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * aspect);
  return radius * margin / Math.sin(Math.min(vertical, horizontal) / 2);
}
