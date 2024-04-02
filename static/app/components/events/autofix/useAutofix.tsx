import {useCallback, useState} from 'react';

import {
  type AutofixData,
  AutofixStepType,
  type GroupWithAutofix,
} from 'sentry/components/events/autofix/types';
import type {Event} from 'sentry/types';
import {
  type ApiQueryKey,
  setApiQueryData,
  useApiQuery,
  useQueryClient,
} from 'sentry/utils/queryClient';
import useApi from 'sentry/utils/useApi';

export type AutofixResponse = {
  autofix: AutofixData | null;
};

const POLL_INTERVAL = 2500;

export const makeAutofixQueryKey = (groupId: string): ApiQueryKey => [
  `/issues/${groupId}/ai-autofix/`,
];

const isPolling = (autofixData?: AutofixData | null) =>
  autofixData?.status === 'PROCESSING';

export const useAutofixData = ({groupId}: {groupId: string}) => {
  const {data} = useApiQuery<AutofixResponse>(makeAutofixQueryKey(groupId), {
    staleTime: Infinity,
    enabled: false,
    notifyOnChangeProps: ['data'],
  });

  return data?.autofix ?? null;
};

export const useAiAutofix = (group: GroupWithAutofix, event: Event) => {
  const api = useApi();
  const queryClient = useQueryClient();

  const [isReset, setIsReset] = useState<boolean>(false);

  const {
    data: apiData,
    isError,
    error,
  } = useApiQuery<AutofixResponse>(makeAutofixQueryKey(group.id), {
    staleTime: 0,
    retry: false,
    refetchInterval: data => {
      if (isPolling(data?.[0]?.autofix)) {
        return POLL_INTERVAL;
      }
      return false;
    },
  });

  const triggerAutofix = useCallback(
    async (instruction: string) => {
      setIsReset(false);
      setApiQueryData<AutofixResponse>(queryClient, makeAutofixQueryKey(group.id), {
        autofix: {
          status: 'PROCESSING',
          run_id: '',
          steps: [
            {
              type: AutofixStepType.DEFAULT,
              id: '1',
              index: 0,
              status: 'PROCESSING',
              title: 'Starting Autofix...',
              progress: [],
            },
          ],
          created_at: new Date().toISOString(),
        },
      });

      try {
        await api.requestPromise(`/issues/${group.id}/ai-autofix/`, {
          method: 'POST',
          data: {
            event_id: event.id,
            instruction,
          },
        });
      } catch (e) {
        // Don't need to do anything, error should be in the metadata
      }
    },
    [queryClient, group.id, api, event.id]
  );

  const reset = useCallback(() => {
    setIsReset(true);
  }, []);

  const autofixData = isReset ? null : apiData?.autofix ?? null;

  return {
    autofixData,
    error,
    isError,
    isPolling: isPolling(autofixData),
    triggerAutofix,
    reset,
  };
};
