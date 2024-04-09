import React, {Fragment} from 'react';
import {browserHistory} from 'react-router';
import styled from '@emotion/styled';

import Alert from 'sentry/components/alert';
import {Breadcrumbs} from 'sentry/components/breadcrumbs';
import FloatingFeedbackWidget from 'sentry/components/feedback/widget/floatingFeedbackWidget';
import * as Layout from 'sentry/components/layouts/thirds';
import {DatePageFilter} from 'sentry/components/organizations/datePageFilter';
import {EnvironmentPageFilter} from 'sentry/components/organizations/environmentPageFilter';
import PageFilterBar from 'sentry/components/organizations/pageFilterBar';
import {ProjectPageFilter} from 'sentry/components/organizations/projectPageFilter';
import SearchBar from 'sentry/components/searchBar';
import {t} from 'sentry/locale';
import {space} from 'sentry/styles/space';
import {fromSorts} from 'sentry/utils/discover/eventView';
import {decodeScalar} from 'sentry/utils/queryString';
import {MutableSearch} from 'sentry/utils/tokenizeSearch';
import {useLocation} from 'sentry/utils/useLocation';
import useOrganization from 'sentry/utils/useOrganization';
import {normalizeUrl} from 'sentry/utils/withDomainRequired';
import {useOnboardingProject} from 'sentry/views/performance/browser/webVitals/utils/useOnboardingProject';
import {DurationChart} from 'sentry/views/performance/database/durationChart';
import {NoDataMessage} from 'sentry/views/performance/database/noDataMessage';
import {isAValidSort, QueriesTable} from 'sentry/views/performance/database/queriesTable';
import {ThroughputChart} from 'sentry/views/performance/database/throughputChart';
import {useSelectedDurationAggregate} from 'sentry/views/performance/database/useSelectedDurationAggregate';
import * as ModuleLayout from 'sentry/views/performance/moduleLayout';
import {ModulePageProviders} from 'sentry/views/performance/modulePageProviders';
import Onboarding from 'sentry/views/performance/onboarding';
import {useSynchronizeCharts} from 'sentry/views/starfish/components/chart';
import {useSpanMetrics} from 'sentry/views/starfish/queries/useSpanMetrics';
import {useSpanMetricsSeries} from 'sentry/views/starfish/queries/useSpanMetricsSeries';
import {ModuleName, SpanMetricsField} from 'sentry/views/starfish/types';
import {QueryParameterNames} from 'sentry/views/starfish/views/queryParameters';
import {ActionSelector} from 'sentry/views/starfish/views/spans/selectors/actionSelector';
import {DomainSelector} from 'sentry/views/starfish/views/spans/selectors/domainSelector';

export function SpanSummary() {
  const organization = useOrganization();
  const location = useLocation();
  const onboardingProject = useOnboardingProject();

  return (
    <React.Fragment>
      <Layout.Header>
        <Layout.HeaderContent>
          <Breadcrumbs
            crumbs={[
              {
                label: 'Performance',
                to: normalizeUrl(`/organizations/${organization.slug}/performance/`),
                preservePageFilters: true,
              },
              {
                label: 'Spans',
              },
              {
                label: 'Span Summary',
              },
            ]}
          />

          <Layout.Title>{t('<transaction name>')}</Layout.Title>
        </Layout.HeaderContent>
      </Layout.Header>

      <Layout.Body>
        <Layout.Main fullWidth>
          <FloatingFeedbackWidget />

          <ModuleLayout.Full>
            <PageFilterBar condensed>
              <EnvironmentPageFilter />
              <DatePageFilter />
            </PageFilterBar>
          </ModuleLayout.Full>
        </Layout.Main>
      </Layout.Body>
    </React.Fragment>
  );
}

const DEFAULT_SORT = {
  field: 'time_spent_percentage()' as const,
  kind: 'desc' as const,
};

function AlertBanner(props) {
  return <Alert {...props} type="info" showIcon />;
}

const FilterOptionsContainer = styled('div')`
  display: flex;
  flex-wrap: wrap;
  gap: ${space(2)};

  @media (min-width: ${p => p.theme.breakpoints.small}) {
    flex-wrap: nowrap;
  }
`;

const SelectorContainer = styled('div')`
  flex-basis: 100%;

  @media (min-width: ${p => p.theme.breakpoints.small}) {
    flex-basis: auto;
  }
`;

const LIMIT: number = 25;

function LandingPageWithProviders() {
  return (
    <ModulePageProviders
      title={[t('Performance'), t('Database')].join(' — ')}
      baseURL="/performance/database"
      features="performance-database-view"
    >
      <SpanSummary />
    </ModulePageProviders>
  );
}

export default LandingPageWithProviders;
