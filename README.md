# Pybg: Parallel job execution for shell scripts
## What is this?
In Unix shell scripts, commands can be executed in parallel using background processes, but pybg simplifies job management with additional features.

Key features:
- Monitoring of job success and failure
- Management of job output logs
- Controling of the number of concurrently running jobs
- Re-execution of failed jobs
- Support for job submission via Slurm Workload Manage

## Install

```sh
pip install git+https://github.com/kamo-naoyuki/pybg
```

> [!CAUTION]
> Windows is not supported

## Example

<table>
<tr>
<th>Unix background process</th>
<th>Pybg style</th>
</tr>
<tr>
<td>
<sub>

```sh
#!/bin/sh
echo Hello World &
sleep 40 &
python -c "print('This is python')" &
wait
```

</sub>
<td>
<sub>

```sh
#!/bin/sh
# 1. Start command pool server or clear all commands from the server
pybg start group_id
# 2. Register jobs to the server
pybg add group_id echo Hello World
pybg add group_id sleep 40
pybg add group_id python -c "print('This is python')"
# 3. Dump commands in text and stop the server
pybg write group_id
# 4 Run commands
pybg run group_id
```

</sub>
</td>
</tr>
</table>


### Template generation

Since job execution with Pybg is not possible without calling multiple Pybg subcommands, it supports generating shell scripts that describe these basic steps.


```sh
pybg tpl > run.sh
```


## Showing

- Showing the output of the job


```sh
pybg show <group-id> <jobid>
```

- Showing the exit-status of the job if the job has been finished


```sh
pybg show <group-id> <jobid> status
```

- Showing the command of the job

```sh
pybg show <group-id> <jobid> command
```

- Showing the jobids of the jobs-group

```sh
pybg show <group-id>
```

- Showing the jobids of failed/succeeded/unfinished jobs

```sh
pybg show <group-id> <failed/succeeded/unfinished>
```
### Suppor for Slurm Workload Manager

Pybg supports job submission via Slurm Workload Manager.
While it is possible to submit jobs by directly calling `sbatch` or `srun` from Pybg, these commands cannot be terminated if Pybg exits abnormally.

<table>
<tr>
<th>Direct submission via `srun`</th>
<th>Using `--slurm-option`</th>
<th>Equivalent to `--slurm-option`</th>
</tr>
<tr>
<td>
<sub>

```sh
pybg add srun -p slurm_partition -c 3 sleep 10
```

</sub>
<td>
<sub>

```sh
pybg add --slurm-option "-p slurm_partition -c 3" sleep 10
```

</sub>
</td>
<td>
<sub>

```sh
pybg add sleep 10 "#SBATCH -p slurm_partition -c 3"
```

</sub>
</td>
</tr>
</table>

