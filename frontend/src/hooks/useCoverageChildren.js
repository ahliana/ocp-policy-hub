import { useEffect, useState } from 'react';
import { apiUrl } from '../config/api';

// Fetches one country's admin-1 breakdown for CountryView. A null/undefined
// `slug` means no country is drilled into yet - deliberately skips the
// fetch rather than hitting the API with an empty parent.
//
// Mirrors useCoverage's refetch-on-change pattern: a scan finishing while a
// country view is open should update its counts, same as the world map.
// `review` ('all' | 'reviewed') is the public review visibility toggle,
// passed straight through from WorldMap/CountryView.
function useCoverageChildren(slug, review) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!slug) {
      setData(null);
      setError(null);
      setIsLoading(false);
      return undefined;
    }

    let isCurrent = true;

    const load = () => {
      setIsLoading(true);
      const params = new URLSearchParams({ parent: slug });
      if (review) params.set('review', review);
      fetch(apiUrl(`/api/coverage/children?${params.toString()}`))
        .then((res) => {
          if (!res.ok) throw new Error(`coverage children fetch failed (${res.status})`);
          return res.json();
        })
        .then((json) => {
          if (!isCurrent) return;
          setData(json);
          setError(null);
          setIsLoading(false);
        })
        .catch((loadError) => {
          if (!isCurrent) return;
          setError(loadError);
          setIsLoading(false);
        });
    };

    load();
    window.addEventListener('policy-data-changed', load);
    return () => {
      isCurrent = false;
      window.removeEventListener('policy-data-changed', load);
    };
  }, [slug, review]);

  return { data, error, isLoading };
}

export default useCoverageChildren;
