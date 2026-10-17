using Microsoft.Extensions.Options;
using System.Diagnostics;
using System.Text.RegularExpressions;

namespace Mole.API.Utils;

public class Version
{
    public string PoresVersion { get; set; } = "";
    public string MoleVersion { get; set; } = "";
    public string APIVersion { get; set; } = "0.6.0";
    public string Build { get; set; } = "101626"; // MMDDYY
}

/// <summary>Versions of the configured MOLE and pores tool, read when the API starts (<see cref="ToolVersionReader"/>), so they are always the tools that run</summary>
public class ToolVersions
{
    public string Mole { get; internal set; } = "";
    public string Pores { get; internal set; } = "";
}

/// <summary>
/// Runs the tools once when the API starts, after the configuration is checked (registered after it). MOLE without
/// arguments prints "WebChemistry Tunnels 2.5.26.10.17, (c) ..." first, the pores tool with --version "pores 2.0.0".
/// Without a version the API does not start.
/// </summary>
public partial class ToolVersionReader(IOptions<Config> options, ToolVersions versions) : IHostedService
{
    [GeneratedRegex(@"^WebChemistry Tunnels (\S+),")]
    private static partial Regex MoleLine();

    [GeneratedRegex(@"^pores (\S+)$")]
    private static partial Regex PoresLine();

    /// <summary>How long a tool may take to report its version</summary>
    public TimeSpan Timeout { get; init; } = TimeSpan.FromSeconds(30);

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        versions.Mole = await ReadAsync("MOLE", options.Value.MOLE, [], MoleLine(), cancellationToken);
        versions.Pores = await ReadAsync("Pores", options.Value.Pores, ["--version"], PoresLine(), cancellationToken);
    }

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    /// <summary>The version from the tool's first line, if it exits with code 0 in time</summary>
    private async Task<string> ReadAsync(string setting, string path, string[] arguments, Regex version, CancellationToken stop)
    {
        var info = new ProcessStartInfo(path) { RedirectStandardOutput = true }; // stderr goes to the API's log
        foreach (var argument in arguments) info.ArgumentList.Add(argument);
        using var process = Process.Start(info)!;
        using var limit = CancellationTokenSource.CreateLinkedTokenSource(stop);
        limit.CancelAfter(Timeout);
        string? line = null, problem = null;
        try
        {
            line = await process.StandardOutput.ReadLineAsync(limit.Token);
            await process.StandardOutput.ReadToEndAsync(limit.Token); // the rest, so that a full pipe never blocks it
            await process.WaitForExitAsync(limit.Token);
            if (process.ExitCode != 0) problem = $"it exited with code {process.ExitCode}";
        }
        catch (OperationCanceledException)
        {
            process.Kill(entireProcessTree: true);
            stop.ThrowIfCancellationRequested(); // the API is stopping
            problem = $"it did not finish within {Timeout.TotalSeconds:0.#} s";
        }

        var match = version.Match(line ?? "");
        if (problem != null || !match.Success)
            throw new OptionsValidationException(nameof(Config), typeof(Config), [$"Config:{setting} '{path}' did not report its version, {problem ?? $"its first line is \"{line}\""}."]);
        return match.Groups[1].Value;
    }
}
