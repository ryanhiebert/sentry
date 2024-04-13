import {Fragment, useMemo} from 'react';
import {browserHistory} from 'react-router';
import styled from '@emotion/styled';

import Pagination from 'sentry/components/pagination';
import {t, tct} from 'sentry/locale';
import {trackAnalytics} from 'sentry/utils/analytics';
import type {ApiQueryKey} from 'sentry/utils/queryClient';
import {decodeList, decodeScalar, decodeSorts} from 'sentry/utils/queryString';
import useFetchReplayList from 'sentry/utils/replays/hooks/useFetchReplayList';
import {MutableSearch} from 'sentry/utils/tokenizeSearch';
import useLocationQuery from 'sentry/utils/url/useLocationQuery';
import useOrganization from 'sentry/utils/useOrganization';
import usePageFilters from 'sentry/utils/usePageFilters';
import useProjectSdkNeedsUpdate from 'sentry/utils/useProjectSdkNeedsUpdate';
import useAllMobileProj from 'sentry/views/replays/detail/useAllMobileProj';
import ReplayTable from 'sentry/views/replays/replayTable';
import {ReplayColumn} from 'sentry/views/replays/replayTable/types';

const MIN_REPLAY_CLICK_SDK = '7.44.0';

function ReplaysList() {
  const organization = useOrganization();

  const query = useLocationQuery({
    fields: {
      cursor: decodeScalar,
      project: decodeList,
      query: decodeScalar,
      sort: value => decodeScalar(value, '-started_at'),
      statsPeriod: decodeScalar,
    },
  });
  const queryKey = useMemo<ApiQueryKey>(() => {
    return [`/organizations/${organization.slug}/replays/`, {query}];
  }, [organization, query]);

  const {
    data: replays,
    getResponseHeader,
    isLoading,
    error,
  } = useFetchReplayList({
    queryKey,
    queryReferrer: 'replayList',
  });
  const pageLinks = getResponseHeader?.('Link') ?? null;

  const {
    selection: {projects},
  } = usePageFilters();

  const {allMobileProj} = useAllMobileProj();

  const {needsUpdate: allSelectedProjectsNeedUpdates} = useProjectSdkNeedsUpdate({
    minVersion: MIN_REPLAY_CLICK_SDK,
    organization,
    projectId: projects.map(String),
  });

  const conditions = useMemo(() => new MutableSearch(query.query), [query.query]);
  const hasReplayClick = conditions.getFilterKeys().some(k => k.startsWith('click.'));

  // browser isn't applicable for mobile projects
  // rage and dead clicks not available yet
  const visibleCols = allMobileProj
    ? [
        ReplayColumn.REPLAY,
        ReplayColumn.OS,
        ReplayColumn.DURATION,
        ReplayColumn.COUNT_ERRORS,
        ReplayColumn.ACTIVITY,
      ]
    : [
        ReplayColumn.REPLAY,
        ReplayColumn.OS,
        ReplayColumn.BROWSER,
        ReplayColumn.DURATION,
        ReplayColumn.COUNT_DEAD_CLICKS,
        ReplayColumn.COUNT_RAGE_CLICKS,
        ReplayColumn.COUNT_ERRORS,
        ReplayColumn.ACTIVITY,
      ];

  return (
    <Fragment>
      <ReplayTable
        referrerLocation={'replay'}
        fetchError={error as Error}
        isFetching={isLoading}
        replays={replays}
        sort={decodeSorts(query.sort)[0]}
        visibleColumns={visibleCols}
        showDropdownFilters
        emptyMessage={
          allSelectedProjectsNeedUpdates && hasReplayClick ? (
            <Fragment>
              {t('Unindexed search field')}
              <EmptyStateSubheading>
                {tct('Field [field] requires an [sdkPrompt]', {
                  field: <strong>'click'</strong>,
                  sdkPrompt: <strong>{t('SDK version >= 7.44.0')}</strong>,
                })}
              </EmptyStateSubheading>
            </Fragment>
          ) : undefined
        }
      />
      <ReplayPagination
        pageLinks={pageLinks}
        onCursor={(cursor, path, searchQuery) => {
          trackAnalytics('replay.list-paginated', {
            organization,
            direction: cursor?.endsWith(':1') ? 'prev' : 'next',
          });
          browserHistory.push({
            pathname: path,
            query: {...searchQuery, cursor},
          });
        }}
      />
    </Fragment>
  );
}

const EmptyStateSubheading = styled('div')`
  color: ${p => p.theme.subText};
  font-size: ${p => p.theme.fontSizeMedium};
`;

const ReplayPagination = styled(Pagination)`
  margin-top: 0;
`;

export default ReplaysList;
