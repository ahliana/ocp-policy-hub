import { useCallback, useEffect, useState } from 'react';
import HelpOutlinedIcon from '@mui/icons-material/HelpOutlined';
import './App.css';
import AgentPanel from './components/AgentPanel';
import AskPolicyBox from './components/AskPolicyBox';
import BackToTopButton from './components/BackToTopButton';
import LeadsInbox from './components/LeadsInbox';
import LogoImage from './assets/ocp-logo.svg';
import PolicyList from './components/PolicyList';
import HelpWindow from './components/HelpWindow';
import { apiUrl } from './config/api';
import { getAdminToken } from './utils/adminAuth';

const WELCOME_TUTORIAL_STORAGE_KEY = 'policy-pulse-welcome-seen';

function App() {
  const [isHelpOpen, setIsHelpOpen] = useState(false);
  const [isFirstRunHelpOpen, setIsFirstRunHelpOpen] = useState(false);
  const [adminRequired, setAdminRequired] = useState(false);
  const [hasAdminToken, setHasAdminToken] = useState(Boolean(getAdminToken()));
  const [placePolicyRequest, setPlacePolicyRequest] = useState(null);
  // Public review visibility (WP-3): posture comes from /health, publicView
  // is the reader-facing toggle it seeds - 'reviewed_only' locks the toggle
  // to 'reviewed' and hides the switch (see WorldMap/PolicyList).
  const [visibilityPosture, setVisibilityPosture] = useState('default_all');
  const [publicView, setPublicView] = useState('all');

  const handleViewPlacePolicies = useCallback(({ slug, name }) => {
    setPlacePolicyRequest({ slug, name, nonce: Date.now() });
  }, []);

  const refreshAdminTokenStatus = useCallback(() => {
    setHasAdminToken(Boolean(getAdminToken()));
  }, []);

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const response = await fetch(apiUrl('/health'));
        if (!response.ok) throw new Error();
        const data = await response.json();
        setAdminRequired(Boolean(data.admin_required));
        const posture = data.public_review_visibility || 'default_all';
        setVisibilityPosture(posture);
        setPublicView(posture === 'default_all' ? 'all' : 'reviewed');
      } catch {
        setAdminRequired(false);
      }
    };

    fetchHealth();
  }, []);

  const markWelcomeTutorialSeen = () => {
    try {
      window.localStorage.setItem(WELCOME_TUTORIAL_STORAGE_KEY, 'true');
    } catch {
      // localStorage can be unavailable in private or restricted browser modes.
    }
  };

  const closeWelcomeTutorial = () => {
    markWelcomeTutorialSeen();
    setIsFirstRunHelpOpen(false);
  };

  useEffect(() => {
    try {
      if (window.localStorage.getItem(WELCOME_TUTORIAL_STORAGE_KEY) !== 'true') {
        setIsFirstRunHelpOpen(true);
      }
    } catch {
      setIsFirstRunHelpOpen(true);
    }
  }, []);

  return (
    <div className="App">
      <header className="App-header">
        <div className="app-header-inner">
          <div className="app-brand">
            <img src={LogoImage} alt="Open Compute Project" className="logo-image" />
            <div className="app-brand-title">
              <h1>Policy Pulse</h1>
            </div>
          </div>
          <nav className="app-header-nav" aria-label="Application navigation">
            <button
              type="button"
              className="app-help-button"
              onClick={() => setIsHelpOpen(true)}
              aria-label="Open help tutorial"
              title="Help"
            >
              <HelpOutlinedIcon fontSize="small" />
              <span>Help</span>
            </button>
          </nav>
        </div>
      </header>
      <HelpWindow
        open={isFirstRunHelpOpen || isHelpOpen}
        onClose={isFirstRunHelpOpen ? closeWelcomeTutorial : () => setIsHelpOpen(false)}
      />
      <main className="App-main">
        <section className="app-stage" aria-label="Policy scanner">
          <AgentPanel
            adminRequired={adminRequired}
            hasAdminToken={hasAdminToken}
            onAdminTokenChange={refreshAdminTokenStatus}
            onViewPlacePolicies={handleViewPlacePolicies}
            publicView={publicView}
            onPublicViewChange={setPublicView}
            showPublicViewToggle={visibilityPosture !== 'reviewed_only'}
          />
        </section>
        <section className="app-stage" aria-label="Ask about policies">
          <AskPolicyBox />
        </section>
        <section className="app-stage" aria-label="Discovered policies">
          <PolicyList
            externalPlace={placePolicyRequest}
            publicView={publicView}
            adminRequired={adminRequired}
            hasAdminToken={hasAdminToken}
          />
          <LeadsInbox adminRequired={adminRequired} hasAdminToken={hasAdminToken} />
        </section>
      </main>
      <BackToTopButton />
    </div>
  );
}

export default App;
