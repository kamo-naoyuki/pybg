# Pybg: Parallel job execution for shell scripts
## What is this?
In Unix shell scripts, commands can be executed in parallel using background processes, but pybg simplifies job management with additional features.

Key features:
- Monitoring of job success and failure
- Management of job output logs
- Controling of the number of concurrently running jobs
- Re-execution of failed jobs
- Support for job submission via Slurm

## Install

```sh
git clone https://github.com/kamo-naoyuki/pybg
pip install ./pybg
```

[!CAUTION]
Windows is not supported

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
echo Hello World &
group sleep 40 &
python -c "print('This is python')" &
wait
```

</sub>
<td>
<sub>

```sh
# 1. Clear all commands from the pool server
pybg clear group
# 2. Register jobs to the server
pybg add group echo Hello World
pybg add group sleep 40
pybg add group python -c "print('This is python')"
# 3. Dump commands in text and stop the pool server
pybg write group
# 4 Run commands
pybg run group
```

</sub>
</td>
</tr>
</table>



### Option

## Template generation


```sh
pybg tpl > run.sh
```


## Showing


```sh
```



### Slurm

### Internal behaviour
