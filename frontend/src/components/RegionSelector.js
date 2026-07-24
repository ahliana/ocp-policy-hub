import * as React from 'react';
import { styled, alpha } from '@mui/material/styles';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import { SimpleTreeView } from '@mui/x-tree-view/SimpleTreeView';
import { TreeItem, treeItemClasses } from '@mui/x-tree-view/TreeItem';
import { apiUrl } from '../config/api';
import { formatLabel } from '../utils/scanTargets';

const SELECTION_GREEN = '#8dc63f';
const SELECTION_GREEN_SOFT = '#f6fbf0';
const SELECTION_GREEN_HOVER = '#eff5e8';

const TOP_LEVEL_IDS = new Set([
  'section:categories',
  'section:tags',
  'section:domains',
]);

// Internal QA groups from groups.yaml that end users should never scan
const HIDDEN_GROUP_PATTERN = /^(test($|_)|sample($|_))/;

export function isHiddenGroup(groupId) {
  return HIDDEN_GROUP_PATTERN.test(groupId);
}

// Re-exported for callers (and RegionSelector.test.js) that historically
// imported formatLabel from here - the single implementation now lives in
// utils/scanTargets.js, shared with the scan-scope summary line.
export { formatLabel };

function countDomainsByValue(domains, getter) {
  const counts = new Map();

  domains.forEach((domain) => {
    const values = getter(domain);
    values.forEach((value) => {
      if (!value) return;
      counts.set(value, (counts.get(value) || 0) + 1);
    });
  });

  return counts;
}

function sortByLabel(items) {
  return [...items].sort((a, b) => a.label.localeCompare(b.label));
}

function buildCountLabel(label, count) {
  return count ? `${label} (${count})` : label;
}

function buildGroupRegionItems(groupId, domains, regionLabels) {
  const regionCounts = countDomainsByValue(domains, (domain) => domain.region || []);

  return sortByLabel(
    [...regionCounts.entries()]
      .map(([region, count]) => ({
        id: `group:${groupId}:region:${region}`,
        value: `group:${groupId}:region:${region}`,
        label: buildCountLabel(regionLabels[region] || formatLabel(region), count),
      })),
  );
}

export function buildTreeData({ groups, groupDomains, regions }) {
  const groupItems = sortByLabel(
    Object.entries(groups)
      .filter(([id]) => !isHiddenGroup(id))
      .map(([id, description]) => ({
        id: `group:${id}`,
        value: `group:${id}`,
        label: description && description !== 'No description'
          ? `${formatLabel(id)} - ${description}`
          : formatLabel(id),
        children: buildGroupRegionItems(id, groupDomains[id] || [], regions),
      })),
  );

  // Pin the catch-all group to the top; everything else stays alphabetical
  const allIndex = groupItems.findIndex((item) => item.id === 'group:all');
  if (allIndex > 0) {
    const [allItem] = groupItems.splice(allIndex, 1);
    groupItems.unshift(allItem);
  }

  return groupItems;
}

async function fetchJson(path, signal) {
  const response = await fetch(apiUrl(path), { signal });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}: ${response.status}`);
  }
  return response.json();
}

async function fetchGroupDomains(groups, signal) {
  const entries = await Promise.all(
    Object.keys(groups).filter((id) => !isHiddenGroup(id)).map(async (groupId) => {
      const params = new URLSearchParams({ group: groupId });
      const response = await fetchJson(`/api/domains?${params.toString()}`, signal);
      return [groupId, response.domains || []];
    }),
  );

  return Object.fromEntries(entries);
}

// 1. Create a Custom Tree Item with your specific styling
const StyledTreeItem = styled(TreeItem)(({ theme }) => ({
  color: theme.palette.text.secondary,
  [`& .${treeItemClasses.content}`]: {
    padding: theme.spacing(0.5, 1),
    margin: theme.spacing(0.2, 0),
    borderRadius: theme.shape.borderRadius,
    '&:hover': {
      backgroundColor: SELECTION_GREEN_SOFT,
    },
    [`&&.${treeItemClasses.selected}, &&.Mui-selected`]: {
      backgroundColor: SELECTION_GREEN_SOFT,
      color: theme.palette.text.primary,
      '&:hover': {
        backgroundColor: SELECTION_GREEN_HOVER,
      },
    },
    [`&&.${treeItemClasses.selected}.${treeItemClasses.focused}, &&.Mui-selected.Mui-focused`]: {
      backgroundColor: SELECTION_GREEN_SOFT,
    },
    [`&&.${treeItemClasses.focused}, &&.Mui-focused`]: {
      backgroundColor: SELECTION_GREEN_SOFT,
    },
  },
  '& .MuiCheckbox-root.Mui-checked, & .MuiCheckbox-root.MuiCheckbox-indeterminate': {
    color: SELECTION_GREEN,
  },
  [`& .${treeItemClasses.groupTransition}`]: {
    marginLeft: 15,
    paddingLeft: 18,
    borderLeft: `1px dashed ${alpha(theme.palette.text.primary, 0.4)}`,
  },
}));

// 2. Build the actual Component
function renderTreeItems(items, onItemMouseDown) {
  return items.map((item) => (
    <StyledTreeItem
      key={item.id}
      itemId={item.id}
      label={item.label}
      onMouseDownCapture={(event) => onItemMouseDown(event, item.id)}
    >
      {item.children ? renderTreeItems(item.children, onItemMouseDown) : null}
    </StyledTreeItem>
  ));
}

function flattenTreeItems(items) {
  return items.flatMap((item) => [
    item,
    ...(item.children ? flattenTreeItems(item.children) : []),
  ]);
}

function getDescendantIds(item) {
  return (item.children || []).flatMap((child) => [
    child.id,
    ...getDescendantIds(child),
  ]);
}

export default function RegionSelector({ selectedItems, onSelectionChange }) {
  const [treeData, setTreeData] = React.useState([]);
  const [status, setStatus] = React.useState('loading');
  const [error, setError] = React.useState('');
  const clickedItemRef = React.useRef(null);

  const itemValueById = React.useMemo(() => {
    const entries = flattenTreeItems(treeData)
      .filter((item) => item.value)
      .map((item) => [item.id, item.value]);
    return new Map(entries);
  }, [treeData]);

  const itemIdByValue = React.useMemo(() => {
    const entries = flattenTreeItems(treeData)
      .filter((item) => item.value)
      .map((item) => [item.value, item.id]);
    return new Map(entries);
  }, [treeData]);

  const treeSelectedItems = React.useMemo(
    () => (selectedItems || []).map((value) => itemIdByValue.get(value) || value),
    [itemIdByValue, selectedItems],
  );

  const descendantIdsById = React.useMemo(() => {
    const entries = flattenTreeItems(treeData).map((item) => [item.id, getDescendantIds(item)]);
    return new Map(entries);
  }, [treeData]);

  React.useEffect(() => {
    const controller = new AbortController();

    async function loadDomainFilters() {
      try {
        setStatus('loading');
        setError('');

        const [groups, regions] = await Promise.all([
          fetchJson('/api/groups', controller.signal),
          fetchJson('/api/regions', controller.signal),
        ]);
        const groupDomains = await fetchGroupDomains(groups, controller.signal);

        setTreeData(buildTreeData({
          groups,
          groupDomains,
          regions,
        }));
        setStatus('ready');
      } catch (loadError) {
        if (loadError.name === 'AbortError') return;
        console.error(loadError);
        setError('Could not load data. Check that the backend is running, then refresh.');
        setStatus('error');
      }
    }

    loadDomainFilters();

    return () => {
      controller.abort();
    };
  }, []);

  const handleSelectedItemsChange = React.useCallback(
    (event, itemIds) => {
      const clickedItemId = clickedItemRef.current;
      clickedItemRef.current = null;
      const descendantIds = descendantIdsById.get(clickedItemId) || [];
      const shouldClearBranch = descendantIds.length > 0
        && treeSelectedItems.some((id) => descendantIds.includes(id));
      const nextItemIds = shouldClearBranch
        ? itemIds.filter((id) => id !== clickedItemId && !descendantIds.includes(id))
        : itemIds;
      const selectableItems = [
        ...new Set(
          nextItemIds
            .filter((id) => !TOP_LEVEL_IDS.has(id))
            .map((id) => itemValueById.get(id) || id),
        ),
      ];
      onSelectionChange?.(event, selectableItems);
    },
    [descendantIdsById, itemValueById, onSelectionChange, treeSelectedItems],
  );

  const handleItemMouseDown = React.useCallback((event, itemId) => {
    const closestTreeItem = event.target.closest?.('[role="treeitem"]');
    if (closestTreeItem === event.currentTarget) {
      clickedItemRef.current = itemId;
    }
  }, []);

  if (status === 'loading') {
    return (
      <Box sx={{ minHeight: 200, width: '100%', display: 'flex', alignItems: 'center' }}>
        <Typography color="text.secondary" variant="body2">
          Loading domains...
        </Typography>
      </Box>
    );
  }

  if (status === 'error') {
    return (
      <Box sx={{ minHeight: 200, width: '100%' }}>
        <Typography color="error" variant="body2">
          {error || 'Could not load domains.'}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ minHeight: 200, flexGrow: 1, width: '100%' }}>
      <SimpleTreeView
        checkboxSelection
        selectionPropagation={{ descendants: true, parents: true }}
        multiSelect
        selectedItems={treeSelectedItems}
        onSelectedItemsChange={handleSelectedItemsChange}
      >
        {renderTreeItems(treeData, handleItemMouseDown)}
      </SimpleTreeView>
    </Box>
  );
}
