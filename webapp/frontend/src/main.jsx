import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import RootErrorBoundary from './components/ui/RootErrorBoundary.jsx';
import './index.css';
import './i18n';   // init i18next as a side effect

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </React.StrictMode>,
);
