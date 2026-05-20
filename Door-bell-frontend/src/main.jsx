/*
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: React entry point that mounts the App component into the #root DOM node
 * inside StrictMode.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
