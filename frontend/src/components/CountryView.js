import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import useCoverageChildren from '../hooks/useCoverageChildren';
import usePanZoom from '../hooks/usePanZoom';
import { joinAdmin1, pluralize } from '../utils/mapCoverage';
import MapTooltip from './MapTooltip';
import RegionPanel from './RegionPanel';

function unitLabel(unit, cov) {
  if (!cov) return `${unit.name}: not yet tracked`;
  return `${unit.name}: ${cov.sources} sources, ${cov.policies} policies`;
}

function Admin1Path({ item, onHover, onHoverEnd, onSelect }) {
  const { unit, cov, bin } = item;
  const tracked = bin !== 'untracked';
  const classes = ['wm-country', `wm-bin-${bin}`];

  return (
    <path
      d={unit.d}
      className={classes.join(' ')}
      data-bin={bin}
      aria-label={unitLabel(unit, cov)}
      tabIndex={tracked ? 0 : undefined}
      role={tracked ? 'button' : undefined}
      vectorEffect="non-scaling-stroke"
      onPointerMove={(event) => onHover(unit, cov, event)}
      onPointerLeave={onHoverEnd}
      onClick={() => onSelect(unit, cov)}
      onKeyDown={tracked ? (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect(unit, cov);
        }
      } : undefined}
    />
  );
}

// One country's admin-1 (state/province) drill-down, entered from a
// drillable country's panel in WorldMap. Self-contained: owns its own
// pan/zoom (via the same usePanZoom hook the world map uses, generalized to
// accept this country's own viewBox/bounds), hover/selection state, and its
// own Escape handling - first press closes an open unit panel, second exits
// to the world view - so WorldMap's Escape effect steps aside entirely
// while this is mounted (see WorldMap.js).
//
// `slug`/`load` come from the DRILLABLE_COUNTRIES registry (config/
// drillableCountries.js) - the frontend-only bridge for the two things
// /api/coverage does not carry: a slug to query /api/coverage/children
// with, and which countries have admin-1 geometry at all.
function CountryView({
  slug, countryName, load, onBack, onSelectPlace,
  onViewPlacePolicies, showScanAction = true, publicView = 'all',
}) {
  const holderRef = useRef(null);
  const svgRef = useRef(null);

  const [geometry, setGeometry] = useState(null);
  const [geometryError, setGeometryError] = useState(null);
  const [hover, setHover] = useState(null);
  const [selection, setSelection] = useState(null);

  const { data: childrenData, error: childrenError, isLoading: childrenLoading } = (
    useCoverageChildren(slug, publicView)
  );

  useEffect(() => {
    let isCurrent = true;
    load()
      .then((mod) => {
        if (isCurrent) setGeometry(mod.default || mod);
      })
      .catch((err) => {
        if (isCurrent) setGeometryError(err);
      });
    return () => {
      isCurrent = false;
    };
  }, [load]);

  // Only recomputed when the lazy-loaded geometry itself changes (once), so
  // this object's identity stays stable across re-renders - usePanZoom
  // resets its viewBox when this identity changes, and would otherwise snap
  // back to the top-left on every unrelated render (e.g. a hover).
  const panZoomConfig = useMemo(() => {
    if (!geometry) return undefined;
    const [x, y, w, h] = geometry.viewBox;
    const viewBox = { x, y, w, h };
    return { viewBox, bounds: { ...viewBox, minW: w / 8, maxW: w } };
  }, [geometry]);
  const panZoom = usePanZoom(svgRef, panZoomConfig);

  const joined = useMemo(
    () => (geometry ? joinAdmin1(geometry.units, childrenData?.children || []) : []),
    [geometry, childrenData],
  );

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return;
      if (selection) {
        setSelection(null);
        return;
      }
      onBack();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selection, onBack]);

  const handleHover = useCallback((unit, cov, event) => {
    const holderRect = holderRef.current?.getBoundingClientRect();
    if (!holderRect) return;
    let x = event.clientX - holderRect.left + 14;
    let y = event.clientY - holderRect.top + 14;
    if (x > holderRect.width - 220) x = event.clientX - holderRect.left - 220;
    if (y > holderRect.height - 90) y = event.clientY - holderRect.top - 90;
    setHover({ id: unit.code, x, y, name: unit.name, cov });
  }, []);
  const handleHoverEnd = useCallback(() => setHover(null), []);

  const handleSelectUnit = useCallback((unit, cov) => {
    setSelection({
      id: unit.code,
      slug: cov?.slug,
      name: unit.name,
      sources: cov?.sources || 0,
      policies: cov?.policies || 0,
      topPolicyNames: cov?.top_policy_names || [],
      cities: cov?.cities,
      isFederal: false,
    });
  }, []);

  const handleSelectFederal = useCallback(() => {
    if (!childrenData) return;
    const { national, parent } = childrenData;
    setSelection({
      id: 'federal',
      slug: parent?.slug,
      name: countryName,
      sources: national.sources,
      policies: national.policies,
      topPolicyNames: national.top_policy_names || [],
      cities: national.cities,
      isFederal: true,
    });
  }, [childrenData, countryName]);

  const handleClosePanel = useCallback(() => setSelection(null), []);

  const isLoading = !geometry || childrenLoading;
  const loadError = geometryError || childrenError;

  if (loadError) {
    return (
      <div className="wm-country-view wm-country-view-message">
        <p className="wm-breadcrumb">
          <button type="button" className="wm-breadcrumb-world" onClick={onBack}>
            &larr; World
          </button>
          {' / '}{countryName}
        </p>
        <p className="wm-error" role="alert">
          Could not load {countryName}&rsquo;s regions right now. Try again, or go back to
          the world map.
        </p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="wm-country-view wm-country-view-message">
        <p className="wm-breadcrumb">
          <button type="button" className="wm-breadcrumb-world" onClick={onBack}>
            &larr; World
          </button>
          {' / '}{countryName}
        </p>
        <p className="wm-loading" role="status">Loading {countryName}&rsquo;s regions&hellip;</p>
      </div>
    );
  }

  const { national, children, totals } = childrenData;
  const childPolicyTotal = totals.policies - national.policies;

  return (
    <div className="wm-country-view">
      <p className="wm-breadcrumb">
        <button type="button" className="wm-breadcrumb-world" onClick={onBack}>
          &larr; World
        </button>
        {' / '}{countryName}
      </p>
      <p className="wm-reconcile">
        {national.policies} federal {pluralize(national.policies, 'policy', 'policies')}
        {' + '}
        {childPolicyTotal} across {children.length} {pluralize(children.length, 'region')}
        {' = '}
        {totals.policies} total
      </p>

      <div className="wm-stage" ref={holderRef}>
        <svg
          ref={svgRef}
          className="wm-svg"
          viewBox={`${panZoom.viewBox.x} ${panZoom.viewBox.y} ${panZoom.viewBox.w} ${panZoom.viewBox.h}`}
          role="group"
          aria-label={`${countryName} regions map`}
          {...panZoom.handlers}
        >
          {joined.map((item) => (
            <Admin1Path
              key={item.unit.code}
              item={item}
              onHover={handleHover}
              onHoverEnd={handleHoverEnd}
              onSelect={handleSelectUnit}
            />
          ))}
        </svg>

        {/* Deliberately not a map shape: a nationwide law is not a place, so
            it never borrows a choropleth color. A German user must never
            read this as a single-Land law. */}
        <button
          type="button"
          className="wm-federal-chip"
          onClick={handleSelectFederal}
          aria-pressed={selection?.isFederal || false}
        >
          <span className="wm-federal-chip-label">Federal / nationwide</span>
          <span className="wm-federal-chip-count">
            {national.sources} {pluralize(national.sources, 'source')}
            {' · '}
            {national.policies} {pluralize(national.policies, 'policy', 'policies')}
          </span>
        </button>

        <div className="wm-controls" role="group" aria-label="Map zoom controls">
          <button
            type="button"
            className="wm-control-btn"
            aria-label="Zoom in"
            onClick={panZoom.zoomIn}
            disabled={!panZoom.canZoomIn}
          >
            +
          </button>
          <button
            type="button"
            className="wm-control-btn"
            aria-label="Zoom out"
            onClick={panZoom.zoomOut}
            disabled={!panZoom.canZoomOut}
          >
            &minus;
          </button>
          <button
            type="button"
            className="wm-control-btn wm-control-reset"
            aria-label="Reset map view"
            onClick={panZoom.reset}
            disabled={!panZoom.canZoomOut}
          >
            Reset
          </button>
        </div>

        <MapTooltip hover={hover} />
        <RegionPanel
          selection={selection}
          onClose={handleClosePanel}
          onSearchPlace={onSelectPlace}
          onViewPlacePolicies={onViewPlacePolicies}
          showScanAction={showScanAction}
        />
      </div>
    </div>
  );
}

export default CountryView;
