using Microsoft.Extensions.Options;
using System.Diagnostics;
using System.Text.RegularExpressions;

namespace Mole.API.Utils;

public class Version
{
    public string PoresVersion { get; set; } = "2.0.0";
    public string MoleVersion { get; set; } = "";
    public string APIVersion { get; set; } = "0.6.0";
    public string Build { get; set; } = "101626"; // MMDDYY
}

/// <summary>The version of the configured MOLE, read when the API starts (<see cref="MoleVersionReader"/>), so it is always the MOLE that runs</summary>
public class MoleVersion
{
    public string Value { get; internal set; } = "";
}

/// <summary>
/// Runs MOLE once without arguments when the API starts, after the configuration is checked (registered after it):
/// its first line names the version, "WebChemistry Tunnels 2.5.26.10.17, (c) ...". Without it the API does not start.
/// </summary>
public partial class MoleVersionReader(IOptions<Config> options, MoleVersion version) : IHostedService
{
    [GeneratedRegex(@"^WebChemistry Tunnels (\S+),")]
    private static partial Regex FirstLine();

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        var mole = options.Value.MOLE;
        using var process = Process.Start(new ProcessStartInfo(mole) { RedirectStandardOutput = true, RedirectStandardError = true })!;
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
        string? line = null;
        try
        {
            line = await process.StandardOutput.ReadLineAsync(timeout.Token);
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException) { process.Kill(entireProcessTree: true); }

        var match = FirstLine().Match(line ?? "");
        if (!match.Success)
            throw new OptionsValidationException(nameof(Config), typeof(Config), [$"Config:MOLE '{mole}' did not report its version, its first line is \"{line}\"."]);
        version.Value = match.Groups[1].Value;
    }

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;
}
