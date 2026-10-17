import React from 'react'
import ReactDOM from 'react-dom/client'
import { Provider } from 'react-redux'
import { BrowserRouter, Route, Routes } from 'react-router'
import App from './App'
import Home from './Home'
import { TooltipProvider } from './components/ui/tooltip'
import { store } from './store'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Provider store={store}>
    <TooltipProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/p/:pid" element={<App />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
    </Provider>
  </React.StrictMode>,
)
