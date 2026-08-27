import * as React from 'react';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardActionArea from '@mui/material/CardActionArea';
import CardContent from '@mui/material/CardContent';
import Typography from '@mui/material/Typography';

// WP-27: outcome language, not implementation language - each card says what
// happens and why you'd pick it, not "standard settings"/"digs deeper".
const cards = [
  {
    id: 'standard',
    title: 'Standard',
    badge: 'Recommended',
    description: 'Checks the government sites we already watch for new and changed policies.',
    tint: '#f8fafc',
    hoverTint: '#f1f5f9',
    selectedTint: '#dbe4ef',
    selectedHoverTint: '#d1d9e3',
    border: '#a3b1da',
  },
  {
    id: 'discover',
    title: 'Discover',
    description: "Searches the web for government sites we don't watch yet, then adds them to the watch list.",
    tint: '#f8fafc',
    hoverTint: '#f1f5f9',
    selectedTint: '#dbe4ef',
    selectedHoverTint: '#d1d9e3',
    border: '#a3b1da',
  },
  {
    id: 'deep',
    title: 'Deep',
    description: 'Rereads every page of the sites we watch, more thoroughly - for when you suspect something was missed.',
    tint: '#fbfaf8',
    hoverTint: '#e0c7ea',
    selectedTint: '#e0d6e4',
    selectedHoverTint: '#c0a5d0',
    border: '#c377e2',
  },
];

// The bounded-cost note (see useCostEstimate's DISCOVERY_COST_NOTE) in short
// form - the card is small, the full sentence lives in the cost-estimate
// output below the cards.
const DISCOVER_PRICE_NOTE = 'Cost varies - bounded per country';

function formatPriceLine(estimate) {
  if (!estimate) return null;
  const low = estimate.estimated_cost_low_usd;
  const high = estimate.estimated_cost_high_usd;
  if (low != null && high != null && Number(low) !== Number(high)) {
    return `est. $${Number(low).toFixed(2)}-$${Number(high).toFixed(2)}`;
  }
  return `est. $${Number(estimate.estimated_cost_usd || 0).toFixed(2)}`;
}

function priceLineFor(cardId, { hasScope, standardEstimate, deepEstimate }) {
  if (!hasScope) return null;
  if (cardId === 'discover') return DISCOVER_PRICE_NOTE;
  if (cardId === 'standard') return formatPriceLine(standardEstimate);
  if (cardId === 'deep') return formatPriceLine(deepEstimate);
  return null;
}

function ModeSelector({
  value = 'standard', onChange, hasScope = false, standardEstimate = null, deepEstimate = null,
}) {
  return (
    <Box
      sx={{
        width: '100%',
        display: 'grid',
        gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
        gap: 1.5,
        minWidth: '150px',
        overflow: 'anywhere',
      }}
    >
      {cards.map((card) => {
        const isSelected = value === card.id;
        const priceLine = priceLineFor(card.id, { hasScope, standardEstimate, deepEstimate });

        return (
          <Card
            key={card.id}
            variant="outlined"
            sx={{
              minWidth: 0,
              borderColor: isSelected ? '#64748b' : card.border,
              backgroundColor: isSelected ? card.selectedTint : card.tint,
              boxShadow: isSelected ? 'inset 0 0 0 1px #64748b' : 'none',
              transition: 'border-color 120ms ease, background-color 120ms ease',
            }}
          >
            <CardActionArea
              disableRipple
              disableTouchRipple
              focusRipple={false}
              onClick={() => onChange?.(card.id)}
              aria-pressed={isSelected}
              sx={{
                height: '100%',
                transition: 'none',
                '&:focus-visible': {
                  outline: '2px solid #4d7c0f',
                  outlineOffset: 2,
                },
                '&:hover': {
                  backgroundColor: isSelected ? card.selectedHoverTint : card.hoverTint,
                },
              }}
            >
              <CardContent sx={{ height: '100%', minWidth: 0, padding: 1.5 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                  <Typography
                    variant="h5"
                    component="div"
                    sx={{
                      color: isSelected ? '#0f172a' : '#334155',
                      fontSize: 'clamp(1rem, 1.6vw, 1.5rem)',
                      overflowWrap: 'anywhere',
                    }}
                  >
                    {card.title}
                  </Typography>
                  {card.badge && (
                    <Box
                      component="span"
                      className="mode-badge"
                    >
                      {card.badge}
                    </Box>
                  )}
                </Box>
                <Typography
                  variant="body2"
                  sx={{
                    color: isSelected ? '#334155' : '#64748b',
                    overflowWrap: 'anywhere',
                  }}
                >
                  {card.description}
                </Typography>
                {priceLine && (
                  <Typography
                    variant="body2"
                    className="mode-price-line"
                    sx={{
                      color: '#475569',
                      fontWeight: 600,
                      marginTop: 0.5,
                      overflowWrap: 'anywhere',
                    }}
                  >
                    {priceLine}
                  </Typography>
                )}
              </CardContent>
            </CardActionArea>
          </Card>
        );
      })}
    </Box>
  );
}

export default ModeSelector;
