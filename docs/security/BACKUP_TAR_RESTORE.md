# TAR backup and restore safety

DockerPilot checks the complete TAR member list before extracting a configuration
or container-data backup. It refuses absolute paths, parent-directory traversal,
unrecognized member types (devices, FIFOs, etc.), and links that would escape
the restore directory. Existing symlink destinations cannot be overwritten or
used as intermediate directories. Regular files are written to temporary files
and atomically replaced, so overwriting an existing hardlink will not mutate
its other names.

Two container-data archive layouts are supported:

- Docker volume backups made from the volume root with a dot-prefix: unpack
  entries directly inside the selected volume mountpoint.
- Directory or bind-mount backups including a top-level directory: strip the
  single matching top-level directory and unpack inside the selected bind mount.

Internal symlinks and hardlinks are supported if their targets remain inside the
restore root. Unsupported or unsafe entries fail the restore instead of being
silently skipped. Configuration imports only accept known configuration
filenames as regular files.

Container restores only read archives from the selected backup directory. A
stored absolute archive path is reduced to its basename so older backups remain
portable. The metadata's named volume or bind source must also match a mount
on the target container.

A failed restore of a directory bind mount rolls back the original directory.
For named Docker volumes, extraction is **not transactional**: a late I/O error
or truncated payload can leave some files updated. Make a separate verified
backup before any production restore. A missing archive, extraction failure,
or unsupported volume type is reported as a failed operation.

The extraction helper uses Python standard library APIs available in Python
3.10+, without requiring the newer tarfile extraction-filter backports.
