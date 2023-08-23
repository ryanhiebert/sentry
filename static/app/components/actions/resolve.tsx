import {css} from '@emotion/react';
import styled from '@emotion/styled';
import {useHover, usePress} from '@react-aria/interactions';
import {motion, Variants} from 'framer-motion';

import {openModal} from 'sentry/actionCreators/modal';
import {Button, LinkButton} from 'sentry/components/button';
import ButtonBar from 'sentry/components/buttonBar';
import {openConfirmModal} from 'sentry/components/confirm';
import CustomCommitsResolutionModal from 'sentry/components/customCommitsResolutionModal';
import CustomResolutionModal from 'sentry/components/customResolutionModal';
import {DropdownMenu, MenuItemProps} from 'sentry/components/dropdownMenu';
import {Tooltip} from 'sentry/components/tooltip';
import {IconChevron, IconReleases} from 'sentry/icons';
import {t} from 'sentry/locale';
import {space} from 'sentry/styles/space';
import {
  GroupStatus,
  GroupStatusResolution,
  GroupSubstatus,
  Project,
  ResolvedStatusDetails,
} from 'sentry/types';
import {trackAnalytics} from 'sentry/utils/analytics';
import {formatVersion, isSemverRelease} from 'sentry/utils/formatters';
import useOrganization from 'sentry/utils/useOrganization';
import {ErrorGremlinBody} from 'sentry/views/issueDetails/actions/resolve/errorGremlinBody';

const peekingGremlinVariants: Variants = {
  hidden: {
    x: 40,
    y: 0,
    rotate: 20,
    scale: 0.5,
    transition: {
      duration: 0.3,
    },
  },
  shown: () => {
    const x = 40 + (Math.random() - 0.5) * 50;
    return {
      x: [40, x, x],
      y: [0, 0, -18],
      rotate: [20, 20, 20],
      scale: [0.5, 0.5, 1],
      transition: {
        duration: 0.6,
      },
    };
  },
};

function SetupReleasesPrompt() {
  return (
    <SetupReleases>
      <IconReleases size="xl" />
      <div>
        <SetupReleasesHeader>
          {t('Resolving is better with Releases')}
        </SetupReleasesHeader>
        {t(
          'Set up Releases so Sentry can bother you when this problem comes back in a future release.'
        )}
      </div>
      <LinkButton
        priority="primary"
        external
        size="xs"
        href="https://docs.sentry.io/product/releases/setup/"
        analyticsEventName="Issue Actions: Resolve Release Setup Prompt Clicked"
        analyticsEventKey="issue_actions.resolve_release_setup_prompt_clicked"
      >
        {t('Set up Releases Now')}
      </LinkButton>
    </SetupReleases>
  );
}

export interface ResolveActionsProps {
  hasRelease: boolean;
  onUpdate: (data: GroupStatusResolution) => void;
  confirmLabel?: string;
  confirmMessage?: React.ReactNode;
  disableDropdown?: boolean;
  disabled?: boolean;
  isAutoResolved?: boolean;
  isResolved?: boolean;
  latestRelease?: Project['latestRelease'];
  priority?: 'primary';
  projectFetchError?: boolean;
  projectSlug?: string;
  shouldConfirm?: boolean;
  size?: 'xs' | 'sm';
}

function ResolveActions({
  size = 'xs',
  isResolved = false,
  isAutoResolved = false,
  confirmLabel = t('Resolve'),
  projectSlug,
  hasRelease,
  latestRelease,
  confirmMessage,
  shouldConfirm,
  disabled,
  disableDropdown,
  priority,
  projectFetchError,
  onUpdate,
}: ResolveActionsProps) {
  const organization = useOrganization();
  const {isHovered, hoverProps} = useHover({});
  const {isPressed, pressProps} = usePress({});

  function handleCommitResolution(statusDetails: ResolvedStatusDetails) {
    onUpdate({
      status: GroupStatus.RESOLVED,
      statusDetails,
      substatus: null,
    });
  }

  function handleAnotherExistingReleaseResolution(statusDetails: ResolvedStatusDetails) {
    onUpdate({
      status: GroupStatus.RESOLVED,
      statusDetails,
      substatus: null,
    });
    trackAnalytics('resolve_issue', {
      organization,
      release: 'anotherExisting',
    });
  }

  function handleCurrentReleaseResolution() {
    if (hasRelease) {
      onUpdate({
        status: GroupStatus.RESOLVED,
        statusDetails: {
          inRelease: latestRelease ? latestRelease.version : 'latest',
        },
        substatus: null,
      });
    }

    trackAnalytics('resolve_issue', {
      organization,
      release: 'current',
    });
  }

  function handleNextReleaseResolution() {
    if (hasRelease) {
      onUpdate({
        status: GroupStatus.RESOLVED,
        statusDetails: {
          inNextRelease: true,
        },
        substatus: null,
      });
    }

    trackAnalytics('resolve_issue', {
      organization,
      release: 'next',
    });
  }

  function renderResolved() {
    return (
      <Tooltip
        title={
          isAutoResolved
            ? t(
                'This event is resolved due to the Auto Resolve configuration for this project'
              )
            : t('Unresolve')
        }
      >
        <Button
          priority="primary"
          size="xs"
          aria-label={t('Unresolve')}
          disabled={isAutoResolved}
          onClick={() =>
            onUpdate({
              status: GroupStatus.UNRESOLVED,
              statusDetails: {},
              substatus: GroupSubstatus.ONGOING,
            })
          }
        />
      </Tooltip>
    );
  }

  function renderDropdownMenu() {
    if (isResolved) {
      return renderResolved();
    }

    const actionTitle = !hasRelease
      ? t('Set up release tracking in order to use this feature.')
      : '';

    const onActionOrConfirm = (onAction: () => void) => {
      openConfirmModal({
        bypass: !shouldConfirm,
        onConfirm: onAction,
        message: confirmMessage,
        confirmText: confirmLabel,
      });
    };

    const isSemver = latestRelease ? isSemverRelease(latestRelease.version) : false;
    const items: MenuItemProps[] = [
      {
        key: 'next-release',
        label: t('The next release'),
        details: actionTitle,
        onAction: () => onActionOrConfirm(handleNextReleaseResolution),
      },
      {
        key: 'current-release',
        label: t('The current release'),
        details: actionTitle
          ? actionTitle
          : latestRelease
          ? `${formatVersion(latestRelease.version)} (${
              isSemver ? t('semver') : t('non-semver')
            })`
          : null,
        onAction: () => onActionOrConfirm(handleCurrentReleaseResolution),
      },
      {
        key: 'another-release',
        label: t('Another existing release\u2026'),
        onAction: () => openCustomReleaseModal(),
      },
      {
        key: 'a-commit',
        label: t('A commit\u2026'),
        onAction: () => openCustomCommitModal(),
      },
    ];

    const isDisabled = !projectSlug ? disabled : disableDropdown;

    return (
      <StyledDropdownMenu
        itemsHidden={!hasRelease}
        items={items}
        trigger={triggerProps => (
          <DropdownTrigger
            {...triggerProps}
            size={size}
            priority={priority}
            aria-label={t('More resolve options')}
            icon={<IconChevron direction="down" size="xs" />}
            disabled={isDisabled}
          />
        )}
        disabledKeys={
          disabled || !hasRelease
            ? ['next-release', 'current-release', 'another-release']
            : []
        }
        menuTitle={hasRelease ? t('Resolved In') : <SetupReleasesPrompt />}
        isDisabled={isDisabled}
      />
    );
  }

  function openCustomCommitModal() {
    openModal(deps => (
      <CustomCommitsResolutionModal
        {...deps}
        onSelected={(statusDetails: ResolvedStatusDetails) =>
          handleCommitResolution(statusDetails)
        }
        orgSlug={organization.slug}
        projectSlug={projectSlug}
      />
    ));
  }

  function openCustomReleaseModal() {
    openModal(deps => (
      <CustomResolutionModal
        {...deps}
        onSelected={(statusDetails: ResolvedStatusDetails) =>
          handleAnotherExistingReleaseResolution(statusDetails)
        }
        organization={organization}
        projectSlug={projectSlug}
      />
    ));
  }

  if (isResolved) {
    return renderResolved();
  }

  return (
    <div style={{position: 'relative'}}>
      <PeekingErrorGremlin
        variants={peekingGremlinVariants}
        initial="hidden"
        animate={isHovered && !isPressed ? 'shown' : 'hidden'}
      >
        <ErrorGremlinBody />
      </PeekingErrorGremlin>
      <Tooltip disabled={!projectFetchError} title={t('Error fetching project')}>
        <ButtonBar merged {...hoverProps} {...pressProps}>
          <ResolveButton
            priority={priority}
            size={size}
            title={t("We'll nag you with a notification if another event is seen.")}
            tooltipProps={{delay: 2000, disabled}}
            onClick={() =>
              openConfirmModal({
                bypass: !shouldConfirm,
                onConfirm: () =>
                  onUpdate({
                    status: GroupStatus.RESOLVED,
                    statusDetails: {},
                    substatus: null,
                  }),
                message: confirmMessage,
                confirmText: confirmLabel,
              })
            }
            disabled={disabled}
          >
            {t('Resolve')}
          </ResolveButton>
          {renderDropdownMenu()}
        </ButtonBar>
      </Tooltip>
    </div>
  );
}

export default ResolveActions;

const PeekingErrorGremlin = styled(motion.div)`
  position: absolute;
`;

const ResolveButton = styled(Button)<{priority?: 'primary'}>`
  box-shadow: none;
  border-radius: ${p => p.theme.borderRadiusLeft};
  ${p =>
    p.priority === 'primary' &&
    css`
      &::after {
        content: '';
        position: absolute;
        top: -1px;
        bottom: -1px;
        right: -1px;
        border-right: solid 1px currentColor;
        opacity: 0.25;
      }
    `}
`;

const DropdownTrigger = styled(Button)`
  box-shadow: none;
  border-radius: ${p => p.theme.borderRadiusRight};
  border-left: none;
`;

/**
 * Used to hide the list items when prompting to set up releases
 */
const StyledDropdownMenu = styled(DropdownMenu)<{itemsHidden: boolean}>`
  ${p =>
    p.itemsHidden &&
    css`
      ul {
        display: none;
      }
    `}
`;

const SetupReleases = styled('div')`
  display: flex;
  flex-direction: column;
  gap: ${space(2)};
  align-items: center;
  padding: ${space(2)} 0;
  text-align: center;
  color: ${p => p.theme.gray400};
  width: 250px;
  white-space: normal;
  font-weight: normal;
`;

const SetupReleasesHeader = styled('h6')`
  font-size: ${p => p.theme.fontSizeMedium};
  margin-bottom: ${space(1)};
`;
