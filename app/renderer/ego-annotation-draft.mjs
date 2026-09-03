export const EGO_SEMANTIC_FIELDS = Object.freeze([
  "semantic_description",
  "body_part",
  "object_name",
  "object_color",
  "source_name",
  "target_name",
  "exception_type",
  "recovery_action",
]);

const copyValues = (values = {}) => Object.fromEntries(
  EGO_SEMANTIC_FIELDS.map((field) => [field, String(values[field] || "")]),
);

export function createEgoDraft(labelCode, values = {}) {
  return {
    labelCode: String(labelCode || ""),
    values: copyValues(values),
  };
}

export function sameStepNewObjectDraft(previous) {
  const draft = createEgoDraft(previous?.labelCode, previous?.values);
  draft.values.object_name = "";
  draft.values.object_color = "";
  draft.values.exception_type = "";
  draft.values.recovery_action = "";
  return draft;
}

export function reuseSameObjectDraft(previous, labelCode = "") {
  const draft = createEgoDraft(labelCode || previous?.labelCode, previous?.values);
  draft.values.exception_type = "";
  draft.values.recovery_action = "";
  return draft;
}

export function labelUsesEgoSemanticFields(label) {
  const fields = new Set((label?.fields || []).map((field) => field?.code));
  return fields.has("semantic_description");
}

export function previousEpisodeForCurrent(episodes = [], currentEpisodeId = "") {
  const index = episodes.findIndex((episode) => episode?.id === currentEpisodeId);
  return index > 0 ? episodes[index - 1] : null;
}

export function egoDraftForLabel(annotations = [], labelCode = "") {
  const normalizedLabelCode = String(labelCode || "");
  if (!normalizedLabelCode) return null;
  for (let index = annotations.length - 1; index >= 0; index -= 1) {
    const annotation = annotations[index];
    const annotationLabelCode = String(
      annotation?.label_code || annotation?.label_slug || "",
    );
    if (annotationLabelCode !== normalizedLabelCode) continue;
    const values = annotation?.attributes || {};
    if (!String(values.semantic_description || "").trim()) continue;
    return createEgoDraft(normalizedLabelCode, values);
  }
  return null;
}
