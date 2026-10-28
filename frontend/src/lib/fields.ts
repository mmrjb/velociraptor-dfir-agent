// Field lookup shared by tables and the inspector. Matches the backend's get_field: a
// top-level column, or one level of nesting written as "A.B".
export function getField(row: Record<string, unknown> | undefined, key: string): unknown {
  if (!row) return undefined;
  if (key in row) return row[key];
  const dot = key.indexOf(".");
  if (dot > 0) {
    const head = row[key.slice(0, dot)];
    if (head && typeof head === "object" && !Array.isArray(head)) return (head as Record<string, unknown>)[key.slice(dot + 1)];
  }
  return undefined;
}
