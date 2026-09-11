import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Navigation from './components/Navigation';
import Dashboard from './pages/Dashboard';
import Reservations from './pages/Reservations';
import Promotions from './pages/Promotions';
import Reviews from './pages/Reviews';
import Notifications from './pages/Notifications';
import './App.css';
function App() {
    return (_jsx(BrowserRouter, { children: _jsxs("div", { className: "app", children: [_jsx(Navigation, {}), _jsx("main", { className: "app-content", children: _jsxs(Routes, { children: [_jsx(Route, { path: "/", element: _jsx(Dashboard, {}) }), _jsx(Route, { path: "/reservations", element: _jsx(Reservations, {}) }), _jsx(Route, { path: "/promotions", element: _jsx(Promotions, {}) }), _jsx(Route, { path: "/reviews", element: _jsx(Reviews, {}) }), _jsx(Route, { path: "/notifications", element: _jsx(Notifications, {}) })] }) })] }) }));
}
export default App;
