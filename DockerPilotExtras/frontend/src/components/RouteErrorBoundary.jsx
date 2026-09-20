import React from 'react'
import {
  failedRouteState,
  reloadCurrentPage,
  shouldResetRouteError,
} from '../utils/routeRecovery.mjs'

class RouteErrorBoundary extends React.Component {
  state = { hasError: false }

  static getDerivedStateFromError() {
    return failedRouteState()
  }

  componentDidCatch(error, errorInfo) {
    console.error('Unable to load DockerPilot Extras view', error, errorInfo)
  }

  componentDidUpdate(previousProps) {
    if (shouldResetRouteError(this.state.hasError, previousProps.resetKey, this.props.resetKey)) {
      this.setState({ hasError: false })
    }
  }

  handleReload = () => {
    reloadCurrentPage(() => window.location.reload())
  }

  render() {
    if (this.state.hasError) {
      const errorCard = (
        <div className="card route-error-card" role="alert">
          <h2 className="card-title">Unable to load this view</h2>
          <p>The application was updated or the route bundle could not be downloaded.</p>
          <button type="button" className="btn btn-primary form-button" onClick={this.handleReload}>
            Reload page
          </button>
        </div>
      )

      if (this.props.fullPage) {
        return <main className="main-content route-loading route-loading-full">{errorCard}</main>
      }

      return errorCard
    }

    return this.props.children
  }
}

export default RouteErrorBoundary
