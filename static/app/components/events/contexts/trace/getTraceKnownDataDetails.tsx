import {Button} from 'sentry/components/button';
import type {KnownDataDetails} from 'sentry/components/events/contexts/utils';
import {generateTraceTarget} from 'sentry/components/quickTrace/utils';
import {t} from 'sentry/locale';
import type {Organization} from 'sentry/types';
import type {Event} from 'sentry/types/event';
import {transactionSummaryRouteWithQuery} from 'sentry/views/performance/transactionSummary/utils';

import type {TraceKnownData} from './types';
import {TraceKnownDataType} from './types';

type Props = {
  data: TraceKnownData;
  event: Event;
  organization: Organization;
  type: TraceKnownDataType;
};

export function getTraceKnownDataDetails({
  data,
  event,
  organization,
  type,
}: Props): KnownDataDetails {
  switch (type) {
    case TraceKnownDataType.TRACE_ID: {
      const traceId = data.trace_id || '';

      if (!traceId) {
        return undefined;
      }

      if (!organization.features.includes('discover-basic')) {
        return {
          subject: t('Trace ID'),
          value: traceId,
        };
      }

      return {
        subject: t('Trace ID'),
        value: traceId,
        actionButton: (
          <Button size="xs" to={generateTraceTarget(event, organization)}>
            {t('Search by Trace')}
          </Button>
        ),
      };
    }

    case TraceKnownDataType.SPAN_ID: {
      return {
        subject: t('Span ID'),
        value: data.span_id || '',
      };
    }

    case TraceKnownDataType.PARENT_SPAN_ID: {
      return {
        subject: t('Parent Span ID'),
        value: data.parent_span_id || '',
      };
    }

    case TraceKnownDataType.OP_NAME: {
      return {
        subject: t('Operation Name'),
        value: data.op || '',
      };
    }

    case TraceKnownDataType.STATUS: {
      return {
        subject: t('Status'),
        value: data.status || '',
      };
    }

    case TraceKnownDataType.TRANSACTION_NAME: {
      const eventTag = event?.tags.find(tag => {
        return tag.key === 'transaction';
      });

      if (!eventTag || typeof eventTag.value !== 'string') {
        return undefined;
      }
      const transactionName = eventTag.value;

      if (!organization.features.includes('performance-view')) {
        return {
          subject: t('Transaction'),
          value: transactionName,
        };
      }

      const to = transactionSummaryRouteWithQuery({
        orgSlug: organization.slug,
        transaction: transactionName,
        projectID: event.projectID,
        query: {},
      });

      return {
        subject: t('Transaction'),
        value: transactionName,
        actionButton: (
          <Button size="xs" to={to}>
            {t('View Summary')}
          </Button>
        ),
      };
    }

    default:
      return undefined;
  }
}
