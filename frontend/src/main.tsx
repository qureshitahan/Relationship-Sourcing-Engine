import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import "./index.css";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Principals from "./pages/Principals";
import Discover from "./pages/Discover";
import Prospects from "./pages/Prospects";
import ProspectDetail from "./pages/ProspectDetail";
import Organizations from "./pages/Organizations";
import OrganizationDetail from "./pages/OrganizationDetail";
import Emails from "./pages/Emails";
import LinkedIn from "./pages/LinkedIn";
import Analytics from "./pages/Analytics";
import LinkedInResponses from "./pages/LinkedInResponses";
import FollowersLinkedIn from "./pages/FollowersLinkedIn";
import Conversations from "./pages/Conversations";
import Agent from "./pages/Agent";
import BulkEmails from "./pages/BulkEmails";
import BulkCampaignPage from "./pages/BulkCampaignPage";
import CampaignWizard from "./pages/CampaignWizard";
import CampaignDashboard from "./pages/CampaignDashboard";
import Calls from "./pages/Calls";
import Guide from "./pages/Guide";
import { clearDiscoverStateOnReload } from "./utils/resetDiscoverOnReload";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // Why "Loading..." used to sit there for minutes: the default is 3 retries
      // with exponential backoff, and while the API was starved of DB
      // connections EVERY attempt burned the full pool timeout before failing.
      // Four attempts x 30s meant a page could spin for two minutes before any
      // error surfaced. One retry still covers a transient blip, but a genuine
      // outage now reaches the error branch in seconds instead of minutes.
      retry: 1,
    },
  },
});

// Must run before the tree mounts, so persisted hooks read post-clear storage.
clearDiscoverStateOnReload();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="agent" element={<Agent />} />
            <Route path="campaigns/new" element={<CampaignWizard />} />
            <Route path="campaigns/:id" element={<CampaignDashboard />} />
            <Route path="bulk" element={<BulkEmails />} />
            <Route path="bulk/:id" element={<BulkCampaignPage />} />
            <Route path="guide" element={<Guide />} />
            <Route path="principals" element={<Principals />} />
            <Route path="discover" element={<Discover />} />
            <Route path="prospects" element={<Prospects />} />
            <Route path="prospects/:id" element={<ProspectDetail />} />
            <Route path="organizations" element={<Organizations />} />
            <Route path="organizations/:id" element={<OrganizationDetail />} />
            <Route path="outreach" element={<Conversations />} />
            <Route path="emails" element={<Emails />} />
            <Route path="linkedin" element={<LinkedIn />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="linkedin-responses" element={<LinkedInResponses />} />
            <Route path="followers-linkedin" element={<FollowersLinkedIn />} />
            <Route path="calls" element={<Calls />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
