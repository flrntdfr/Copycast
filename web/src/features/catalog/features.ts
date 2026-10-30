import {
  columnSizingFeature,
  columnVisibilityFeature,
  createExpandedRowModel,
  rowExpandingFeature,
  rowSelectionFeature,
  tableFeatures,
} from "@tanstack/react-table";

/** The Catalog table's features: selection, expandable details, hideable columns, fixed widths. */
export const catalogFeatures = tableFeatures({
  rowSelectionFeature,
  rowExpandingFeature,
  columnVisibilityFeature,
  columnSizingFeature,
  expandedRowModel: createExpandedRowModel(),
});

export type CatalogFeatures = typeof catalogFeatures;
